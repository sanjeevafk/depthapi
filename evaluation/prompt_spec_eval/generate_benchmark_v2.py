import json
import os
import random
import re
from pathlib import Path

from evaluation.corpus_supabase import rest_get


def _sanitize_text(text: str, max_len: int = 180) -> str:
    t = re.sub(r"[\r\n\t]+", " ", str(text or ""))
    t = re.sub(r"[`{}\[\]<>|\\]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_len:
        t = t[:max_len].rsplit(" ", 1)[0].strip()
    return t


def _topic_from_chunk(content: str, fallback: str) -> str:
    content = " ".join(str(content or "").split())
    for marker in ("# ", "## ", "### "):
        if marker in content:
            fragment = content.split(marker, 1)[1].split("\n", 1)[0]
            fragment = fragment.strip("# ")
            if 2 <= len(fragment) <= 80:
                return fragment
    words = [w.strip("`*(){}[],:;.") for w in content.split() if len(w.strip("`*(){}[],:;.")) > 3]
    topic = " ".join(words[:6]) or fallback
    return topic[:80].strip()


def _topic_from_path(path: str | None, fallback_name: str | None = None) -> str:
    if not path:
        return "this topic"
    if path.startswith("file://"):
        path = path[7:]
    base = Path(path).name
    if base.lower() in {"readme.md", "index.md", "README.md"}:
        base = Path(path).parent.name or (fallback_name or "")
    base = re.sub(r"\.[a-zA-Z0-9]+$", "", base)
    base = base.replace("-", " ").replace("_", " ")
    base = re.sub(r"\s+", " ", base).strip()
    return base[:80] if base else "this topic"


def _spec(depth: str, task: str, reasoning: str, style: str, caps: list[str] | None = None, topic: str | None = None) -> dict:
    return {
        "topic": topic,
        "depth": depth,
        "task": task,
        "reasoning": reasoning,
        "style": style,
        "capabilities": caps or [],
    }


def main() -> None:
    size = 20
    random.seed(42)

    params = {
        "select": "id,document_id,content,metadata,chunk_order,knowledge_documents(id,filename,source_url,metadata)",
        "limit": "400",
        "order": "created_at.desc",
        "content": "not.is.null",
    }
    r = rest_get("knowledge_chunks", params=params)
    r.raise_for_status()
    allowed_namespaces = {"system_design"}
    raw_rows = [
        row
        for row in r.json()
        if len(str(row.get("content") or "")) > 120
        and (row.get("metadata") or {}).get("namespace") in allowed_namespaces
    ]
    if len(raw_rows) < size:
        raise RuntimeError("Not enough corpus chunks returned from Supabase")

    random.shuffle(raw_rows)
    rows = []
    seen_docs = set()
    for row in raw_rows:
        doc_id = row.get("document_id")
        if doc_id in seen_docs:
            continue
        seen_docs.add(doc_id)
        rows.append(row)
        if len(rows) >= size:
            break
    if len(rows) < size:
        rows = raw_rows[:size]

    templates = [
        {"category": "accessible_simplification", "expected": "Explain clearly with defined terms, short sentences, and a practical workflow tie.",
         "spec": ("accessible", "explain", "direct", "normal", [])},
        {"category": "accessible_simplification", "expected": "Explain with simple terms and a concrete example.",
         "spec": ("accessible", "explain", "direct", "concise", [])},
        {"category": "socratic_reasoning", "expected": "Ask one targeted diagnostic question instead of explaining.",
         "spec": ("accessible", "explain", "socratic", "normal", ["requires_context"])},
        {"category": "guided_reasoning", "expected": "Use ordered steps with a checkpoint question and a Next step line.",
         "spec": ("accessible", "explain", "guided", "concise", [])},
        {"category": "debate_compare", "expected": "Present balanced arguments for both sides, then give conditional conclusion.",
         "spec": ("technical", "compare", "debate", "normal", [])},
        {"category": "task_compare", "expected": "Compare two options with explicit differences and contextual recommendation.",
         "spec": ("technical", "compare", "direct", "normal", [])},
        {"category": "task_brainstorm", "expected": "Provide 5 distinct ideas with tradeoffs and best-use guidance.",
         "spec": ("technical", "brainstorm", "direct", "normal", [])},
        {"category": "task_brainstorm", "expected": "Provide diverse ideas across process/tooling/architecture levers.",
         "spec": ("accessible", "brainstorm", "guided", "normal", [])},
        {"category": "debate_compare", "expected": "Balanced debate for two approaches with decision criteria.",
         "spec": ("technical", "compare", "debate", "academic", [])},
        {"category": "accessible_simplification", "expected": "Explain clearly with minimal jargon and a short example.",
         "spec": ("accessible", "explain", "direct", "normal", [])},
    ]

    cases = []
    topics = []
    for row in rows:
        doc = row.get("knowledge_documents") or {}
        meta = row.get("metadata") or {}
        source_name = meta.get("source_name") or doc.get("metadata", {}).get("source_name")
        path = (
            doc.get("filename")
            or doc.get("metadata", {}).get("relative_path")
            or meta.get("relative_path")
            or meta.get("source_url")
            or ""
        )
        topic = _topic_from_path(path, fallback_name=source_name)
        if not topic or topic == "this topic":
            topic = _topic_from_chunk(row.get("content") or "", fallback="this topic")
        topics.append(topic)

    for idx, row in enumerate(rows[:size]):
        doc = row.get("knowledge_documents") or {}
        meta = row.get("metadata") or {}
        source_name = meta.get("source_name") or doc.get("metadata", {}).get("source_name")
        content = str(row.get("content") or "")
        path = (
            doc.get("filename")
            or doc.get("metadata", {}).get("relative_path")
            or meta.get("relative_path")
            or meta.get("source_url")
            or ""
        )
        topic = _topic_from_path(path, fallback_name=source_name)
        if not topic or topic == "this topic":
            topic = _topic_from_chunk(content, fallback=str(doc.get("filename") or "this topic"))
        category = templates[idx % len(templates)]["category"]
        expected = templates[idx % len(templates)]["expected"]
        depth, task, reasoning, style, caps = templates[idx % len(templates)]["spec"]

        query = _sanitize_text(f"Explain {topic} in clear terms.")
        runtime = {}
        if "requires_context" in caps:
            runtime["conversation_context"] = f"User said: I saw {topic} but got lost in the details."
            query = _sanitize_text(f"Can you help me understand {topic}?")
        if "requires_search" in caps or "requires_citations" in caps:
            source = doc.get("source_url") or doc.get("filename") or "local-corpus"
            runtime["search_context"] = f"Source: {source}\nContent: {content.strip()[:800]}"
            query = _sanitize_text(f"Answer with citations: what is important about {topic}?")
        if "requires_diagram" in caps:
            runtime["diagram_type"] = "flowchart TD"
            query = _sanitize_text(f"Explain {topic} with a diagram.")

        if task == "compare":
            other_topic = topics[(idx + 1) % len(topics)]
            if other_topic == topic:
                other_topic = topics[(idx + 2) % len(topics)]
            query = _sanitize_text(f"Compare {topic} vs {other_topic} for a production system.")
        if task == "brainstorm":
            query = _sanitize_text(f"Brainstorm approaches for {topic} in a production system.")
        if reasoning == "guided" and task == "explain":
            query = _sanitize_text(f"Guide me through understanding {topic} step by step.")
        if reasoning == "socratic" and task == "explain":
            query = _sanitize_text(f"I am confused about {topic}. What should I focus on first?")

        prompt_spec = _spec(depth, task, reasoning, style, caps, topic=topic)
        cases.append({
            "case_id": f"benchmark-v2-{idx:02d}",
            "query": query,
            "prompt_spec": prompt_spec,
            "expected_behavior": expected,
            "category": category,
            "runtime": runtime if runtime else {},
            "corpus_chunks": [
                {
                    "chunk_id": row.get("id"),
                    "doc_id": doc.get("id") or row.get("document_id"),
                    "source": doc.get("source_url") or doc.get("filename"),
                }
            ],
            "tags": ["benchmark_v2", category],
        })

    out_path = Path("evaluation/prompt_spec_eval/benchmark_v2_20cases.json")
    out_path.write_text(json.dumps(cases, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Wrote {len(cases)} cases to {out_path}")


if __name__ == "__main__":
    main()
