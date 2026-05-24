import json
from pathlib import Path

from corpus_supabase import rest_get


def count_table(table: str) -> int | None:
    r = rest_get(table, params={"select": "*", "limit": "1"}, count=True)
    r.raise_for_status()
    cr = r.headers.get("content-range", "")
    if "/" not in cr:
        return None
    return int(cr.rsplit("/", 1)[1])


def sample_table(table: str) -> dict:
    r = rest_get(table, params={"select": "*", "limit": "1"})
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


def main() -> None:
    out_dir = Path("results/corpus")
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = ["knowledge_collections", "knowledge_documents", "knowledge_chunks"]
    summary = {"tables": {}, "pgvector": {}, "indexes": {}}
    for table in tables:
        sample = sample_table(table)
        if table == "knowledge_chunks":
            emb = sample.get("embedding")
            if isinstance(emb, str):
                summary["pgvector"]["embedding_dimension_observed"] = len([x for x in emb.strip("[]").split(",") if x.strip()])
        summary["tables"][table] = {
            "row_count": count_table(table),
            "columns": list(sample.keys()),
            "sample": {k: ("<vector>" if k == "embedding" else v) for k, v in sample.items()},
        }
    summary["pgvector"]["embedding_column"] = "knowledge_chunks.embedding"
    summary["pgvector"]["expected_dimension"] = 768
    summary["indexes"]["from_migrations"] = [
        "idx_chunks_embedding_hnsw USING hnsw (embedding vector_cosine_ops)",
        "idx_chunks_fts_gin USING gin (fts_tokens)",
        "idx_chunks_fts_simple_gin USING gin (fts_tokens_simple)",
        "idx_chunks_metadata_gin USING gin (metadata jsonb_path_ops)",
        "idx_chunks_doc_order (document_id, chunk_order)",
    ]
    path = out_dir / "schema_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
