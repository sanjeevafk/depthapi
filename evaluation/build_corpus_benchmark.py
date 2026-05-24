import json
import random
import re
from pathlib import Path

from corpus_supabase import rest_get


CATEGORIES = [
    ("factual_lookup", "What does the corpus state about {topic}?"),
    ("conceptual_explanation", "Explain the concept of {topic} from corpus evidence."),
    ("implementation_debugging", "What implementation caveat is documented for {topic}?"),
    ("multi_hop_synthesis", "Summarize key ideas about {topic} from multiple contexts."),
    ("citation_heavy", "Answer with citations: what is important about {topic}?"),
    ("retrieval_ambiguity", "Disambiguate the meaning of {topic} in this corpus."),
]


def topic_from_row(row: dict) -> str:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    content = " ".join(str(row.get("content") or "").split())
    for marker in ["**`", "`", "# "]:
        if marker in content:
            fragment = content.split(marker, 1)[1].split(marker.strip(), 1)[0]
            if 2 <= len(fragment) <= 80:
                return fragment.strip("#`* ")
    words = [w.strip("`*(){}[],:;.") for w in content.split() if len(w.strip("`*(){}[],:;.")) > 3]
    topic = " ".join(words[:6]) or str(meta.get("source_name") or "this technical topic")
    return topic[:80].strip()


def clamp_query(text: str, max_len: int = 180) -> str:
    t = " ".join((text or "").split()).strip()
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "?"


def sanitize_topic(topic: str, max_len: int = 64) -> str:
    t = re.sub(r"[\r\n\t]+", " ", str(topic or ""))
    t = re.sub(r"[`{}\[\]<>|\\]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -,:;.")
    if len(t) > max_len:
        t = t[:max_len].rsplit(" ", 1)[0].strip(" -,:;.")
    return t or "this topic"


def sanitize_query_text(text: str, max_len: int = 180) -> str:
    q = re.sub(r"[\r\n\t]+", " ", str(text or ""))
    q = re.sub(r"[`{}\[\]<>|\\]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if len(q) > max_len:
        q = q[:max_len]
    return q


def main(size: int = 60) -> None:
    random.seed(42)
    params = {
        "select": "id,document_id,content,metadata,chunk_order,knowledge_documents(id,filename,source_url,metadata)",
        "limit": str(size * 2),
        "order": "created_at.desc",
        "content": "not.is.null",
    }
    r = rest_get("knowledge_chunks", params=params)
    r.raise_for_status()
    rows = [row for row in r.json() if len(str(row.get("content") or "")) > 120]
    random.shuffle(rows)
    samples = []
    for idx, row in enumerate(rows[:size]):
        cat, template = CATEGORIES[idx % len(CATEGORIES)]
        doc = row.get("knowledge_documents") or {}
        topic = sanitize_topic(topic_from_row({**row, **doc}))
        content = " ".join(str(row.get("content") or "").split())
        claim = content.split(". ", 1)[0][:120]
        question = template.format(topic=topic)
        if cat == "factual_lookup":
            question = f"What does the corpus state about {topic}?"
        elif cat == "conceptual_explanation":
            question = f"Explain {topic} in simple technical terms."
        elif cat == "implementation_debugging":
            question = f"What implementation caveat is documented for {topic}?"
        if idx % 17 == 0:
            cat = "missing_answer"
            question = f"If the corpus contains no support, say so: what is the production policy for nonexistent feature XQZ-{idx}?"
        elif idx % 13 == 0:
            cat = "adversarial_conflicting_context"
            question = f"Resolve conflicting evidence about {topic} and cite the strongest source."
        question = clamp_query(sanitize_query_text(question, max_len=170))
        samples.append({
            "id": f"corpus-{idx:03d}",
            "query": question,
            "question": question,
            "ground_truth": content[:800],
            "relevant_doc_ids": [doc.get("id") or row.get("document_id")],
            "relevant_chunk_ids": [row.get("id")],
            "difficulty": ["easy", "medium", "hard"][idx % 3],
            "category": cat,
            "expected_citations": [{
                "doc_id": doc.get("id") or row.get("document_id"),
                "chunk_id": row.get("id"),
                "source": doc.get("source_url") or doc.get("filename"),
            }],
            "prompt_spec": {
                "depth": ["surface", "detailed", "expert", "academic"][idx % 4],
                "tone": "objective",
                "format": "markdown",
                "include_citations": True,
            },
            "metadata": {"category": cat, "difficulty": ["easy", "medium", "hard"][idx % 3]},
        })
    out = Path("benchmark_corpus.json")
    out.write_text(json.dumps(samples, indent=2), encoding="utf-8")
    print(f"Wrote {len(samples)} samples to {out}")


if __name__ == "__main__":
    main()
