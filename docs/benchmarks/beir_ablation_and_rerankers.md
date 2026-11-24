# BEIR Ablation, Multi-Hop HotpotQA, and Reranker Evaluation

Recorded on **2026-11-24** on Google Colab T4 GPU (`--model google/embeddinggemma-2 --batch-size 16 --max-seq-length 512 --fp16 --rerank --rerank-top-k 25`, weighted RRF `1.5` dense / `0.5` lexical).

## 1. SciFact: 256d vs. 768d & Top-25 Rerank (`5,183` docs, `300` queries)

| Strategy | 256d NDCG@10 | 768d NDCG@10 | 768d Recall@10 | 768d MRR@10 |
|---|---:|---:|---:|---:|
| `dense` | 0.8009 | **0.8166** | 0.9021 | 0.7959 |
| `lexical` | 0.5694 | 0.5694 | 0.6803 | 0.5381 |
| `hybrid_rrf` | 0.7459 | **0.7575** | **0.9206** | 0.7108 |
| `hybrid_graph_1hop` | 0.7062 | 0.7133 | 0.8634 | 0.6712 |
| `hybrid_graph_2hop` | 0.7167 | 0.7208 | 0.8893 | 0.6736 |
| `hybrid_rrf_rerank` | 0.7480 | **0.7601** | 0.8936 | 0.7229 |

- **256d vs. 768d:** Full 768d adds `+0.0157` NDCG@10 on `dense` and pushes `hybrid_rrf` Recall@10 to `0.9206`, at 3× the vector storage/RAM.
- **Weighted RRF (`1.5 / 0.5`):** Improved `hybrid_rrf` on SciFact 256d from `0.7312` (1:1 baseline) to `0.7459`.

## 2. Multi-Hop HotpotQA (`5,000` docs, `200` queries, 256d)

Prose concept-graph extraction produced `26,131` concepts and `64,912` cross-concept edges.

| Strategy | NDCG@10 | Recall@10 | MRR@10 | p50 (ms) | p95 (ms) |
|---|---:|---:|---:|---:|---:|
| `dense` | 0.9469 | 0.9625 | 0.9772 | 66.45 | 104.26 |
| `lexical` | 0.8694 | 0.8850 | 0.9696 | 0.43 | 0.64 |
| `hybrid_rrf` | 0.9428 | 0.9675 | 0.9840 | 0.08 | 0.10 |
| `hybrid_graph_1hop` | 0.8975 | **0.9725** | 0.9325 | 0.56 | 2.02 |
| `hybrid_graph_2hop` | 0.8949 | 0.9700 | 0.9325 | 2.54 | 7.38 |
| `hybrid_rrf_rerank` | **0.9559** | **0.9725** | **0.9912** | 399.08 | 617.50 |

- **`hybrid_rrf_rerank` (top-25)** won across all three metrics.
- **`hybrid_graph_1hop`** matched the reranker's **`0.9725` Recall@10** in **`0.56 ms`** (vs. `399.08 ms` for reranking), validating DepthAPI's query router enabling graph hops only on multi-hop/lineage queries.

## 3. Reranker Findings & Upgrade Candidates

`BAAI/bge-reranker-base` (278M) won on the 3 harder/larger/multi-hop benchmarks (**FiQA** `0.3322`, **SciDocs** `0.1703`, **HotpotQA** `0.9559`) and lost to `google/embeddinggemma-2` on **SciFact**, **ArguAna**, and **NFCorpus**, confirming that `rerank_skip_similarity` gating on strong dense hits is the right default.

### Recommended Future Drop-In `CrossEncoder` Upgrades (Apache 2.0)
- `cross-encoder/ettin-reranker-400m-v1` (401M, 8K context, MTEB(eng, v2) Retrieval `0.6091`)
- `cross-encoder/ettin-reranker-150m-v1` (151M, 8K context, MTEB(eng, v2) Retrieval `0.5994`)
- `Alibaba-NLP/gte-reranker-modernbert-base` (150M, ModernBERT)
- `BAAI/bge-reranker-v2-m3` (568M, multilingual)

### Larger Open-Weight GPU Alternatives
- `mixedbread-ai/mxbai-rerank-base-v2` (0.5B) / `mxbai-rerank-large-v2` (1.5B, Apache 2.0)
- `Qwen/Qwen3-Reranker-0.6B` / `4B` / `8B` (Apache 2.0, 32K context; causal yes/no prompt format)
