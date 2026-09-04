"""
In-tree Model Context Protocol (MCP) server for DepthAPI.
Exposes cognitive depth query, document ingestion, graph exploration,
Karpathy LLM-Wiki vault reading, and compiled Rust vault linting to agents
(Claude Code, Antigravity, Cursor, Hermes).
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from api.routers.query import QueryRequest
from api.routers.query import query as execute_query
from api.services.rag.graph.concept_extractor import extract_concepts_and_edges
from api.services.security.api_key_auth import ApiKeyRecord
from api.services.wiki.vault_manager import get_vault_manager

try:
    import depth_engine
    _HAS_DEPTH_ENGINE = True
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False


SERVER_NAME = "depthapi-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

TOOL_DEFINITIONS = [
    {
        "name": "depthapi_query",
        "description": "Query DepthAPI dual-indexed knowledge system with OKF cognitive depth tuning (1-5).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query or question.",
                },
                "depth": {
                    "type": "integer",
                    "description": "Cognitive depth level (1-2: direct concept summaries, 3-4: scoped hybrid 1-hop, 5: deep 2-hop graph + rerank).",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 3,
                },
                "collection_id": {
                    "type": "string",
                    "description": "Optional UUID of knowledge collection.",
                },
                "save_to_wiki": {
                    "type": "boolean",
                    "description": "Whether to file the synthesized Q&A insight back to the wiki vault.",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "depthapi_ingest",
        "description": "Parse and chunk raw text, extracting deterministic concepts and graph relations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "raw_text": {
                    "type": "string",
                    "description": "Raw markdown or text content to ingest.",
                },
                "filename": {
                    "type": "string",
                    "description": "Original file name or title.",
                    "default": "document.md",
                },
                "collection_name": {
                    "type": "string",
                    "description": "Target collection name.",
                },
            },
            "required": ["raw_text"],
        },
    },
    {
        "name": "depthapi_explore_graph",
        "description": "Explore connected concept graph nodes and relationships up to N hops.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept_name": {
                    "type": "string",
                    "description": "Seed concept name or slug to explore.",
                },
                "hops": {
                    "type": "integer",
                    "description": "Traversal depth (1 or 2 hops).",
                    "minimum": 1,
                    "maximum": 2,
                    "default": 1,
                },
            },
            "required": ["concept_name"],
        },
    },
    {
        "name": "depthapi_read_wiki",
        "description": "Read a concept note directly from the Karpathy LLM-Wiki vault.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept_name": {
                    "type": "string",
                    "description": "Name or slug of the concept note to read.",
                },
            },
            "required": ["concept_name"],
        },
    },
    {
        "name": "depthapi_lint_wiki",
        "description": "Lint the wiki vault using high-speed Rust engine to detect broken [[WikiLinks]], orphan notes, and cycles.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


class DepthApiMcpServer:
    """Standard Model Context Protocol JSON-RPC server for DepthAPI."""

    def __init__(self, api_key: str = "mcp-internal-key") -> None:
        self.api_key = api_key
        self._fake_key_record = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="pro", is_pro=True)

    def list_tools(self) -> list[dict[str, Any]]:
        return TOOL_DEFINITIONS

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatches tool execution by name."""
        try:
            if name == "depthapi_query":
                return await self._tool_query(arguments)
            elif name == "depthapi_ingest":
                return await self._tool_ingest(arguments)
            elif name == "depthapi_explore_graph":
                return await self._tool_explore_graph(arguments)
            elif name == "depthapi_read_wiki":
                return await self._tool_read_wiki(arguments)
            elif name == "depthapi_lint_wiki":
                return await self._tool_lint_wiki(arguments)
            else:
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                }
        except Exception as exc:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Error executing tool '{name}': {exc!s}"}],
            }

    async def _tool_query(self, args: dict[str, Any]) -> dict[str, Any]:
        query_text = args.get("query", "").strip()
        if not query_text:
            return {"isError": True, "content": [{"type": "text", "text": "Query text cannot be empty."}]}

        depth = int(args.get("depth", 3))
        collection_id = args.get("collection_id")
        save_to_wiki = bool(args.get("save_to_wiki", False))

        req = QueryRequest(
            query=query_text,
            depth=depth,
            collection_id=collection_id,
            save_to_wiki=save_to_wiki,
        )

        class FakeRequest:
            headers: dict[str, str] = {}

        try:
            resp = await execute_query(req, FakeRequest(), self._fake_key_record)  # type: ignore[arg-type]
            output = {
                "answer": resp.answer,
                "cognitive_depth": resp.metadata.get("cognitive_depth", depth),
                "confidence": resp.metadata.get("confidence", "high"),
                "citations": resp.citations,
                "contexts_count": len(resp.contexts),
                "saved_to_wiki": resp.metadata.get("saved_to_wiki", False),
            }
            return {
                "isError": False,
                "content": [{"type": "text", "text": json.dumps(output, indent=2)}],
            }
        except Exception as exc:
            # Provide direct concept search fallback if full server pipeline is offline
            manager = get_vault_manager()
            concept = manager.read_concept(query_text)
            if concept:
                return {
                    "isError": False,
                    "content": [{
                        "type": "text",
                        "text": f"Concept Vault Match for '{query_text}':\n\n{concept['content']}",
                    }],
                }
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Query execution failed: {exc!s}"}],
            }

    async def _tool_ingest(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_text = args.get("raw_text", "")
        filename = args.get("filename", "document.md")

        # Parse & extract concepts deterministically
        extracted = extract_concepts_and_edges(
            raw_text=raw_text,
            document_title=filename,
        )

        concept_names = [c.name for c in extracted.concepts]
        edge_summaries = [f"{e.source_concept} -[{e.relation_type}]-> {e.target_concept}" for e in extracted.edges]

        # Update vault with extracted concepts
        manager = get_vault_manager()
        manager.export_concepts_to_vault(
            concepts=[c.model_dump() for c in extracted.concepts],
            edges=[e.model_dump() for e in extracted.edges],
        )

        result = {
            "status": "ingested",
            "filename": filename,
            "concepts_extracted": concept_names,
            "edges_extracted": edge_summaries,
            "vault_synced": True,
        }
        return {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        }

    async def _tool_explore_graph(self, args: dict[str, Any]) -> dict[str, Any]:
        concept_name = args.get("concept_name", "").strip()
        hops = int(args.get("hops", 1))

        manager = get_vault_manager()
        note = manager.read_concept(concept_name)
        if not note:
            return {
                "isError": False,
                "content": [{"type": "text", "text": f"Concept '{concept_name}' not found in knowledge vault."}],
            }

        all_concepts = {c["name"].lower(): c for c in manager.list_concepts()}
        visited = set()
        to_visit = [(concept_name.lower(), 0)]
        subgraph: list[dict[str, Any]] = []

        while to_visit:
            curr, depth = to_visit.pop(0)
            if curr in visited or depth > hops:
                continue
            visited.add(curr)

            c_info = all_concepts.get(curr)
            if c_info:
                subgraph.append({
                    "concept": c_info["name"],
                    "depth": depth,
                    "links": c_info.get("links", []),
                })
                if depth < hops:
                    for neighbor in c_info.get("links", []):
                        if neighbor.lower() not in visited:
                            to_visit.append((neighbor.lower(), depth + 1))

        return {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps({"root": concept_name, "subgraph": subgraph}, indent=2)}],
        }

    async def _tool_read_wiki(self, args: dict[str, Any]) -> dict[str, Any]:
        concept_name = args.get("concept_name", "").strip()
        manager = get_vault_manager()
        note = manager.read_concept(concept_name)
        if not note:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Concept note '{concept_name}' not found in wiki vault."}],
            }

        return {
            "isError": False,
            "content": [{"type": "text", "text": note["content"]}],
        }

    async def _tool_lint_wiki(self, _args: dict[str, Any]) -> dict[str, Any]:
        manager = get_vault_manager()
        report = manager.lint_vault()
        return {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(report, indent=2)}],
        }

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Processes a single JSON-RPC 2.0 message."""
        method = message.get("method")
        msg_id = message.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }
        elif method == "notifications/initialized":
            return None
        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {},
            }
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": self.list_tools(),
                },
            }
        elif method == "tools/call":
            params = message.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            tool_result = await self.call_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": tool_result,
            }
        else:
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found",
                    },
                }
            return None

    async def run_stdio(self) -> None:
        """Runs the JSON-RPC server over stdin/stdout."""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
            except Exception:
                continue

            resp = await self.handle_message(msg)
            if resp is not None:
                resp_str = json.dumps(resp) + "\n"
                sys.stdout.write(resp_str)
                sys.stdout.flush()


def main() -> None:
    """CLI entrypoint for running DepthAPI MCP server."""
    server = DepthApiMcpServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
