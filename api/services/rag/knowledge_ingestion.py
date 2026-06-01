"""RAG Ingestion Worker for DepthAPI.
Processes documents from the queue, chunks them, and generates embeddings.
"""

import hashlib
import ipaddress
import re
import socket
import ssl
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpcore
import httpx
import structlog
import tiktoken
from api.auth import get_supabase_admin
from api.adapters.supabase_adapter import SupabaseHTTPClient
from api.services.rag.context_processing import rough_token_count
from api.services.rag.embeddings import get_embedding_service

logger = structlog.get_logger(__name__)

MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10MB

def _resolve_and_validate_url(url: str) -> tuple[str, str]:
    """Resolve the hostname once, validate the IP, and return (safe_fetch_url, host_header).

    Prevents DNS rebinding TOCTOU: we pin the resolved IP into the fetch URL so
    httpx never performs a second DNS lookup that could return a different address.
    Raises ValueError if the URL targets a blocked host or network.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme}")

    # Hard-coded blocklist catches obvious cases before resolution.
    _blocked = {
        "localhost", "127.0.0.1", "::1",
        "metadata.google.internal",
        "169.254.169.254",  # AWS/GCP/Azure IMDS
    }
    if hostname.lower() in _blocked:
        raise ValueError(f"URL targets a blocked host: {hostname}")

    # Single DNS resolution — validate immediately.
    try:
        # Prefer IPv4 for consistency; fall back to IPv6 if unavailable.
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not infos:
            raise ValueError(f"DNS resolution returned no addresses for {hostname}")
        # Use the first returned address.
        ip_addr = infos[0][4][0]
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for {hostname}: {exc}") from exc

    ip = ipaddress.ip_address(ip_addr)
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise ValueError(f"URL resolves to a blocked IP range: {ip_addr}")

    # Build a fetch URL that replaces the hostname with the resolved IP so that
    # httpx connects directly — no second DNS resolution can occur.
    port = parsed.port
    netloc_with_ip = f"[{ip_addr}]:{port}" if ":" in ip_addr else (
        f"{ip_addr}:{port}" if port else ip_addr
    )
    safe_url = parsed._replace(netloc=netloc_with_ip).geturl()
    # Return both the pinned URL and the original Host header value.
    host_header = f"{hostname}:{port}" if port else hostname
    return safe_url, host_header

class IngestionWorker:
    def __init__(self, worker_id: str = "default-worker"):
        self.worker_id = worker_id
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        self.embed_service = get_embedding_service()
        self.chunk_size = 512  # Semantic target tokens.
        self.chunk_overlap = 80 # Reserved for legacy fallback only.
        self._link_re = re.compile(r"https?://")
        self._anchor_re = re.compile(r"\]\(#.+?\)")

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _is_valid_chunk(self, text: str) -> bool:
        words = text.split()
        if len(words) < 30:
            return False
        link_ratio = len(self._link_re.findall(text)) / max(1, len(words))
        if link_ratio > 0.2:
            return False
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) >= 3:
            anchor_lines = [ln for ln in lines if self._anchor_re.search(ln)]
            if anchor_lines and len(anchor_lines) / len(lines) > 0.6:
                return False
        return True

    async def run_once(self):
        """Perform one cycle of the worker: claim, process, release."""
        supabase: SupabaseHTTPClient = get_supabase_admin()
        if not supabase:
            logger.error("ingestion_worker_db_unavailable")
            return

        # 1. Dequeue a job using our SQL function
        try:
            rpc_res = await supabase.rpc(
                "dequeue_ingestion_job", 
                {"p_worker_id": self.worker_id}
            ).execute()
            
            jobs = rpc_res.data
            if not jobs:
                return # No work to do
                
            job = jobs[0]
            job_id = job["job_id"]
            document_id = job["document_id"]
            
            logger.info("ingestion_job_claimed", job_id=job_id, document_id=document_id)
            
            # 2. Process the document
            try:
                await self.process_document(supabase, document_id)
                
                # 3. Mark as completed
                await supabase.table("knowledge_ingestion_queue")\
                    .update(
                        {
                            "status": "completed",
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                            "last_error": None,
                        }
                    )\
                    .eq("id", job_id).execute()
                    
                logger.info("ingestion_job_completed", job_id=job_id)
                
            except Exception as inner_exc:
                logger.error("ingestion_processing_failed", job_id=job_id, error=str(inner_exc))
                try:
                    await supabase.table("knowledge_ingestion_queue")\
                        .update({"status": "failed", "last_error": str(inner_exc)})\
                        .eq("id", job_id).execute()
                except Exception as update_exc:
                    logger.error("ingestion_status_update_failed", 
                                 job_id=job_id, 
                                 update_error=str(update_exc), 
                                 original_error=str(inner_exc))

        except Exception as exc:
            logger.error("ingestion_worker_cycle_failed", error=str(exc))

    async def process_document(self, supabase: SupabaseHTTPClient, document_id: str):
        """Fetch, chunk, embed, and store a document."""
        # Fetch document metadata
        doc_res = await supabase.table("knowledge_documents").select("*").eq("id", document_id).single().execute()
        doc = doc_res.data
        if not doc:
            raise ValueError("Document not found")

        # For MVP: assume raw text is in metadata or we fetch from source_url
        content = await self.fetch_content(doc)
        if not content:
            raise ValueError("Document content is empty")

        # Chunking
        chunks = self.chunk_text_with_metadata(
            content,
            doc_id=str(document_id),
            source_name=str(doc.get("filename") or doc.get("source_url") or "document"),
            source_url=doc.get("source_url"),
        )
        chunks_text = [chunk["content"] for chunk in chunks]
        
        # Batch Embedding
        embeddings = await self.embed_service.create_embeddings(chunks_text)
        if len(embeddings) != len(chunks_text):
            raise ValueError("Embedding response count mismatch for chunk batch")
        
        # Prepare for DB insert
        chunk_rows = []
        seen_hashes: set[str] = set()
        chunk_order = 0
        for i, (chunk, vector) in enumerate(zip(chunks, embeddings)):
            text = chunk["content"]
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                logger.debug("duplicate_chunk_skipped", document_id=document_id, chunk_order=i)
                continue
            seen_hashes.add(content_hash)
            metadata = dict(doc.get("metadata", {}) or {})
            metadata.update(
                {
                    "doc_id": chunk["doc_id"],
                    "chunk_id": chunk["chunk_id"],
                    "section_title": chunk.get("section_title", ""),
                    "chunking_version": "v3-semantic-local",
                }
            )
            chunk_rows.append({
                "document_id": document_id,
                "content": text,
                "content_hash": content_hash,
                "embedding": vector,
                "token_count": int(chunk.get("token_count") or len(self.tokenizer.encode(text))),
                "chunk_order": chunk_order,
                "metadata": metadata,
            })
            chunk_order += 1

        # Atomic Insert (Batch)
        # Conflict target uses stable order for idempotent retries/reprocessing.
        if chunk_rows:
            await supabase.table("knowledge_chunks").upsert(
                chunk_rows,
                on_conflict="document_id,chunk_order",
            ).execute()

    async def fetch_content(self, doc: Dict[str, Any]) -> str:
        """Fetch raw text from various sources."""
        metadata = doc.get("metadata", {})
        if "raw_text" in metadata:
            return metadata["raw_text"]
        source_url = doc.get("source_url")
        if source_url and str(source_url).startswith(("http://", "https://")):
            # Resolve DNS once, validate the IP, then connect via the pinned IP URL.
            # This prevents TOCTOU DNS rebinding: httpx never performs a second lookup.
            safe_url, host_header = _resolve_and_validate_url(str(source_url))
            parsed_original = urlparse(str(source_url))
            hostname = parsed_original.hostname
            logger.debug("fetching_external_content", url_host=hostname)
            timeout = httpx.Timeout(15.0, connect=5.0)

            if parsed_original.scheme == "https":
                # Use httpcore directly so we can set sni_hostname independently of the
                # URL authority. The URL authority points at the pinned IP (no second DNS
                # resolution); extensions["sni_hostname"] tells the TLS layer to present
                # the original hostname for SNI and certificate validation.
                ssl_ctx = ssl.create_default_context()
                parsed_ip = urlparse(safe_url)
                port = parsed_ip.port or 443
                ip_host = parsed_ip.hostname or ""
                target = parsed_ip.path or "/"
                if parsed_ip.query:
                    target = f"{target}?{parsed_ip.query}"
                async with httpcore.AsyncConnectionPool(
                    ssl_context=ssl_ctx,
                    max_connections=1,
                ) as core_pool:
                    core_response = await core_pool.handle_async_request(
                        httpcore.Request(
                            method=b"GET",
                            url=httpcore.URL(
                                scheme=b"https",
                                host=ip_host.encode(),  # pinned IP — no re-resolution
                                port=port,
                                target=target.encode(),
                            ),
                            headers=[(b"host", host_header.encode())],
                            extensions={"sni_hostname": hostname.encode()},
                        )
                    )
                    body = b"".join([chunk async for chunk in core_response.aiter_raw()])
                    await core_response.aclose()
                if core_response.status < 200 or core_response.status >= 300:
                    raise ValueError(f"HTTP {core_response.status} fetching {hostname}")
            else:
                # Plain HTTP: connect to pinned IP directly, no TLS concerns.
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=False,
                    headers={"Host": host_header},
                ) as client:
                    response = await client.get(safe_url)
                    response.raise_for_status()
                    body = response.content

            if len(body) > MAX_CONTENT_SIZE:
                raise ValueError(f"Response exceeds max size of {MAX_CONTENT_SIZE} bytes")
            return body.decode(errors="replace").strip()

        return ""

    def chunk_text_with_metadata(
        self,
        text: str,
        *,
        doc_id: str = "local-document",
        source_name: str = "document",
        source_url: str | None = None,
    ) -> list[dict[str, Any]]:
        """Heading-aware semantic chunking that preserves code/table blocks."""
        clean = self._clean_text(text)
        if not clean:
            return []

        try:
            from scripts.ingest_corpus.semantic_chunker import HierarchicalSemanticChunker

            chunker = HierarchicalSemanticChunker(
                max_tokens=self.chunk_size,
                version="v3-semantic-local",
                source_type="markdown",
            )
            semantic_chunks = chunker.chunk_document(
                text=clean,
                doc_id=doc_id,
                source_name=source_name,
                source_url=source_url,
                tags=[],
                breadcrumbs=[source_name] if source_name else None,
            )
            out: list[dict[str, Any]] = []
            for idx, chunk in enumerate(semantic_chunks):
                content = self._clean_text(chunk.content)
                if not self._is_valid_chunk(content):
                    continue
                hierarchy = (chunk.metadata or {}).get("hierarchy") or []
                section_title = str(hierarchy[-1]) if hierarchy else ""
                out.append(
                    {
                        "content": content,
                        "doc_id": doc_id,
                        "chunk_id": f"{doc_id}#c{idx:04d}",
                        "section_title": section_title,
                        "token_count": int(chunk.token_count or rough_token_count(content)),
                        "chunk_order": idx,
                    }
                )
            if out:
                return out
        except Exception as exc:
            logger.warning("semantic_chunking_failed_fallback", error=str(exc))

        return self._legacy_chunk_text_with_metadata(clean, doc_id=doc_id)

    def _legacy_chunk_text_with_metadata(self, text: str, *, doc_id: str) -> list[dict[str, Any]]:
        """Token-window fallback used only if semantic chunking fails."""
        tokens = self.tokenizer.encode(text)
        chunks: list[dict[str, Any]] = []
        
        start = 0
        chunk_order = 0
        while start < len(tokens):
            end = start + self.chunk_size
            chunk_tokens = tokens[start:end]
            chunk_text = self._clean_text(self.tokenizer.decode(chunk_tokens))
            if self._is_valid_chunk(chunk_text):
                chunks.append(
                    {
                        "content": chunk_text,
                        "doc_id": doc_id,
                        "chunk_id": f"{doc_id}#c{chunk_order:04d}",
                        "section_title": "",
                        "token_count": len(chunk_tokens),
                        "chunk_order": chunk_order,
                    }
                )
                chunk_order += 1
            start += (self.chunk_size - self.chunk_overlap)
            
        return chunks

    def chunk_text(self, text: str) -> List[str]:
        """Backward-compatible text-only chunking wrapper."""
        return [chunk["content"] for chunk in self.chunk_text_with_metadata(text)]
