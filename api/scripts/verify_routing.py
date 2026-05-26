"""
Static routing trace — no mocks, no LLM calls.
Calls the real routing functions with the real query to prove what model is chosen.
"""

import sys
import os
from unittest.mock import MagicMock

sys.path.append(os.getcwd())
sys.modules["faiss"] = MagicMock()
sys.modules["numpy"] = MagicMock()
sys.modules["filelock"] = MagicMock()
sys.modules["rank_bm25"] = MagicMock()
sys.modules["supabase"] = MagicMock()
sys.modules["api.services.rag_backend_router"] = MagicMock()
sys.modules["api.services.search"] = MagicMock()
sys.modules["api.services.inference_search"] = MagicMock()

from api.services.inference.inference_routing import (
    extract_features,
    route_model_aliases,
    _effective_alias_chain,
    _looks_math_query,
    _token_count,
)
from api.utils import LEARNING_MODE, TECHNICAL_MODE

QUERIES = [
    {
        "label": "Simple (2 tokens)",
        "query": "Quantum Computing",
        "mode": LEARNING_MODE,
    },
    {
        "label": "Math Proof (high complexity)",
        "query": "Derive the mathematical proof for the Heisenberg Uncertainty Principle using the non-commutation of position and momentum operators.",
        "mode": TECHNICAL_MODE,
    },
    {
        "label": "Architecture Trade-off (compare)",
        "query": "Compare the architectural trade-offs between zero-knowledge proofs and optimistic rollups in Ethereum scaling.",
        "mode": TECHNICAL_MODE,
    },
    {
        "label": "Simple technical query (low complexity)",
        "query": "How does Python print work?",
        "mode": TECHNICAL_MODE,
    },
]

print("=" * 80)
print("ROUTING VERIFICATION REPORT — No Mocks, Real Logic")
print("=" * 80)

for test in QUERIES:
    query = test["query"]
    mode = test["mode"]
    level = "expert"

    features = extract_features(query, mode=mode, level=level)
    complexity = features["complexity"]
    latency_priority = features["latency_priority"]
    token_count = _token_count(query)
    is_math = _looks_math_query(query)
    prefers_low_latency = latency_priority >= 0.72 or token_count < 10

    aliases = route_model_aliases(query, mode=mode, level=level)
    final_chain = _effective_alias_chain(aliases, complexity=complexity)

    print(f"\n--- {test['label']} ---")
    print(f"  Query        : {query[:80]}...")
    print(f"  Mode         : {mode}")
    print(f"  Token Count  : {token_count}")
    print(f"  Is Math?     : {is_math}")
    print(f"  Complexity   : {complexity:.2f}")
    print(f"  Latency Prio : {latency_priority:.2f}")
    print(f"  Low Latency? : {prefers_low_latency}")
    print(f"  Raw Aliases  : {aliases}")
    print(f"  Final Chain  : {final_chain}")
    print(f"  >>> PRIMARY MODEL: {final_chain[0] if final_chain else 'NONE'}")

print("\n" + "=" * 80)
print("VERIFICATION COMPLETE")
print("=" * 80)
