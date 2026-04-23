"""RAG Ingestion Worker for DepthAPI.
Processes documents from the queue, chunks them, and generates embeddings.
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
import structlog
import tiktoken
from api.auth import get_supabase_admin
from api.adapters.supabase_adapter import SupabaseHTTPClient
from api.services.embeddings import get_embedding_service

logger = structlog.get_logger(__name__)

class IngestionWorker:
    def __init__(self, worker_id: str = "default-worker"):
        self.worker_id = worker_id
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        self.embed_service = get_embedding_service()
        self.chunk_size = 512  # Tokens
        self.chunk_overlap = 100 # Tokens

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
                await supabase.table("knowledge_ingestion_queue")\
                    .update({"status": "failed", "last_error": str(inner_exc)})\
                    .eq("id", job_id).execute()

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
        chunks_text = self.chunk_text(content)
        
        # Batch Embedding
        embeddings = await self.embed_service.create_embeddings(chunks_text)
        if len(embeddings) != len(chunks_text):
            raise ValueError("Embedding response count mismatch for chunk batch")
        
        # Prepare for DB insert
        chunk_rows = []
        seen_hashes: set[str] = set()
        for i, (text, vector) in enumerate(zip(chunks_text, embeddings)):
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                logger.debug("duplicate_chunk_skipped", document_id=document_id, chunk_order=i)
                continue
            seen_hashes.add(content_hash)
            chunk_rows.append({
                "document_id": document_id,
                "content": text,
                "content_hash": content_hash,
                "embedding": vector,
                "token_count": len(self.tokenizer.encode(text)),
                "chunk_order": i,
                "metadata": doc.get("metadata", {})
            })

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
            
        # Placeholder for URL/Storage fetching
        source_url = doc.get("source_url")
        if source_url and str(source_url).startswith(("http://", "https://")):
            logger.debug("fetching_external_content", url=source_url)
            timeout = httpx.Timeout(15.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(str(source_url))
                response.raise_for_status()
                return response.text.strip()
            
        return ""

    def chunk_text(self, text: str) -> List[str]:
        """Simple recursive-style chunking using token counts."""
        tokens = self.tokenizer.encode(text)
        chunks = []
        
        start = 0
        while start < len(tokens):
            end = start + self.chunk_size
            chunk_tokens = tokens[start:end]
            chunks.append(self.tokenizer.decode(chunk_tokens))
            start += (self.chunk_size - self.chunk_overlap)
            
        return chunks
