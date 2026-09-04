import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd


def _safe_mean(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce")
    return s.mean()


def _coverage(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce")
    return s.notna().mean()


def canonical_id(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    text = text.strip().lower()
    text = re.sub(r"^doc(?:ument)?[_:\-\s]+", "", text)
    text = re.sub(r"^chunk[_:\-\s]+", "", text)
    text = re.sub(r"[\s/|:]+", "-", text)
    text = re.sub(r"[^a-z0-9#._-]+", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_.")
    return text


def _retrieved_ids(row: pd.Series, key: str) -> list[str]:
    contexts = row.get("contexts")
    if not isinstance(contexts, list):
        return []
    values = []
    for ctx in contexts:
        if not isinstance(ctx, dict):
            continue
        candidates = [
            ctx.get(key),
            ctx.get("document_id") if key == "doc_id" else None,
            ctx.get("id") if key == "chunk_id" else None,
        ]
        metadata = ctx.get("metadata")
        if isinstance(metadata, dict):
            candidates.extend(
                [
                    metadata.get(key),
                    metadata.get("document_id") if key == "doc_id" else None,
                    metadata.get("doc_id") if key == "doc_id" else None,
                    metadata.get("chunk_id") if key == "chunk_id" else None,
                ]
            )
        citation = ctx.get("citation")
        if isinstance(citation, dict):
            candidates.extend(
                [
                    citation.get(key),
                    citation.get("document_id") if key == "doc_id" else None,
                    citation.get("chunk_id") if key == "chunk_id" else None,
                ]
            )
        for candidate in candidates:
            normalized = canonical_id(candidate)
            if normalized:
                values.append(normalized)
    return values


def _expected_ids(row: pd.Series, key: str) -> list[str]:
    values = row.get(key)
    aliases = {
        "relevant_doc_ids": ["expected_doc_ids", "doc_ids", "documents"],
        "relevant_chunk_ids": ["expected_chunk_ids", "chunk_ids", "chunks"],
    }
    out = []
    if isinstance(values, list):
        out.extend(values)
    for alias in aliases.get(key, []):
        alias_values = row.get(alias)
        if isinstance(alias_values, list):
            out.extend(alias_values)
    return [normalized for normalized in (canonical_id(v) for v in out) if normalized]


def _recall_at_k(expected: list[str], retrieved: list[str], k: int = 5):
    if not expected:
        return None
    return len(set(expected) & set(retrieved[:k])) / len(set(expected))


def _mrr(expected: list[str], retrieved: list[str]):
    if not expected:
        return None
    expected_set = set(expected)
    for idx, item in enumerate(retrieved, start=1):
        if item in expected_set:
            return 1 / idx
    return 0.0


def _precision_at_k(expected: list[str], retrieved: list[str], k: int = 5):
    retrieved_k = [item for item in retrieved[:k] if item]
    if not retrieved_k:
        return None
    if not expected:
        return None
    return len(set(expected) & set(retrieved_k)) / len(set(retrieved_k))


def _citation_ids(row: pd.Series, key: str) -> list[str]:
    citations = row.get("citations")
    if not isinstance(citations, list):
        return []
    values = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        candidates = [
            citation.get(key),
            citation.get("document_id") if key == "doc_id" else None,
        ]
        metadata = citation.get("metadata")
        if isinstance(metadata, dict):
            candidates.extend([metadata.get(key), metadata.get("document_id") if key == "doc_id" else None])
        for candidate in candidates:
            normalized = canonical_id(candidate)
            if normalized:
                values.append(normalized)
    return values


def analyze_and_report(results: list[dict[str, Any]], output_dir: str):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    raw_path = Path(output_dir) / "raw_results.json"
    with open(raw_path, "w") as f:
        json.dump(results, f, indent=2, default=float)
    if not results:
        return
    df = pd.json_normalize(results)
    for id_kind, expected_col, metric_prefix in [
        ("chunk_id", "relevant_chunk_ids", "chunk"),
        ("doc_id", "relevant_doc_ids", "doc"),
    ]:
        recalls = []
        mrrs = []
        precisions = []
        for _, row in df.iterrows():
            expected = _expected_ids(row, expected_col)
            retrieved = _retrieved_ids(row, id_kind)
            recalls.append(_recall_at_k(expected, retrieved, 5))
            mrrs.append(_mrr(expected, retrieved))
            precisions.append(_precision_at_k(expected, retrieved, 5))
        df[f"retrieval.{metric_prefix}_recall_at_5"] = recalls
        df[f"retrieval.{metric_prefix}_mrr"] = mrrs
        df[f"retrieval.{metric_prefix}_precision_at_5"] = precisions
    df["retrieval.context_count"] = df.apply(lambda r: len(r.get("contexts")) if isinstance(r.get("contexts"), list) else 0, axis=1)
    df["retrieval.citation_grounding_accuracy"] = df.apply(
        lambda r: _precision_at_k(
            _expected_ids(r, "relevant_chunk_ids") + _expected_ids(r, "relevant_doc_ids"),
            _citation_ids(r, "chunk_id") + _citation_ids(r, "doc_id"),
            5,
        ),
        axis=1,
    )
    df.to_csv(Path(output_dir) / "results.csv", index=False)

    metrics = [
        "judge.depth_compliance",
        "judge.answer_quality",
        "judge.citation_accuracy",
        "judge.faithfulness",
        "deepeval.deepeval_relevancy",
        "deepeval.deepeval_faithfulness",
        "ragas.ragas_answer_relevancy",
        "ragas.ragas_faithfulness",
        "retrieval.chunk_recall_at_5",
        "retrieval.chunk_precision_at_5",
        "retrieval.chunk_mrr",
        "retrieval.doc_recall_at_5",
        "retrieval.doc_precision_at_5",
        "retrieval.doc_mrr",
        "retrieval.citation_grounding_accuracy",
        "retrieval.context_count",
    ]
    depth = df[df["system"] == "depthapi"]
    base = df[df["system"] == "langchain_baseline"]

    report = Path(output_dir) / "summary_comparison.md"
    with report.open("w") as f:
        f.write("# DepthAPI Evaluation Benchmark Report\n\n")
        f.write("## Overall Comparison\n\n| Metric | DepthAPI | Baseline |\n|---|---|---|\n")
        for m in metrics:
            if m in df.columns:
                f.write(f"| {m} | {_safe_mean(depth[m]):.4f} | {_safe_mean(base[m]):.4f} |\n")
        f.write("\n## Evaluator Success/Coverage\n\n")
        f.write("| Metric | DepthAPI Coverage | Baseline Coverage |\n|---|---|---|\n")
        for m in metrics:
            if m in df.columns:
                f.write(f"| {m} | {_coverage(depth[m]):.2%} | {_coverage(base[m]):.2%} |\n")

    print(f"Reports generated in {output_dir}")
