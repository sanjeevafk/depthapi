import json
import random
import uuid
from typing import List, Dict, Any

TECHNICAL_QUERIES = [
    "How does asyncio event loop work in Python?",
    "Explain the difference between threading and multiprocessing in Python.",
    "What are the best practices for structuring a large FastAPI application?",
    "Describe the Raft consensus algorithm and its leader election process.",
    "How does PostgreSQL implement MVCC?",
    "What is the time complexity of looking up a value in a Python dict and why?",
    "Explain how Redis achieves persistence.",
    "What are the trade-offs between GraphQL and REST?",
    "How does garbage collection work in CPython?",
    "Describe the architecture of Kubernetes control plane.",
    "In the local DepthAPI corpus, which chunking version marks semantically chunked local ingestion metadata?",
    "What exact fallback answer should DepthAPI use when retrieved context is missing support?",
    "Which retrieval trace file records rerank scores and selected final contexts for local RAG queries?",
    "What internal local RAG namespace is used for trusted corpus retrieval when no collection is specified?",
    "Which local ingestion metadata fields are required to connect chunks back to their section title and document ID?",
    "What implementation quirk prevents DepthAPI retrieval metrics from working when doc IDs are not slug-normalized?",
    "Which prompt instruction prevents copied passages and asks for abstraction over repetition?",
    "What local RAG setting limits the selected contexts before synthesis to reduce token bloat?",
]

def generate_benchmark_dataset(size: int = 120) -> List[Dict[str, Any]]:
    """Generate a diverse benchmark dataset with PromptSpec combinations."""
    dataset = []
    
    # Stratified sampling parameters
    depths = ["surface", "detailed", "expert", "academic"]
    tones = ["objective", "educational", "critical", "concise"]
    formats = ["markdown", "bullet_points", "essay", "code_heavy"]
    
    for i in range(size):
        query = random.choice(TECHNICAL_QUERIES) + f" (Variant {i})"
        prompt_spec = {
            "depth": random.choice(depths),
            "tone": random.choice(tones),
            "format": random.choice(formats),
            "include_citations": random.choice([True, False])
        }
        
        is_blind = query in TECHNICAL_QUERIES[-8:]
        dataset.append({
            "id": str(uuid.uuid4()),
            "query": query,
            "prompt_spec": prompt_spec,
            "metadata": {
                "category": "llm_blind" if is_blind else "technical",
                "difficulty": random.choice(["easy", "medium", "hard"])
            },
            "category": "llm_blind" if is_blind else "technical",
            "difficulty": "hard" if is_blind else random.choice(["easy", "medium", "hard"]),
        })
        
    return dataset

if __name__ == "__main__":
    dataset = generate_benchmark_dataset(120)
    with open("benchmark_dataset.json", "w") as f:
        json.dump(dataset, f, indent=2)
    print(f"Generated {len(dataset)} test cases in benchmark_dataset.json")
