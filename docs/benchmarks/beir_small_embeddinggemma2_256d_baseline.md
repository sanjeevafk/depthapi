# BEIR Small Datasets Baseline (`google/embeddinggemma-2`, 256d)

Recorded on **2026-11-24** using Google Colab T4 GPU (`--model google/embeddinggemma-2 --dim 256 --max-seq-length 512 --fp16 --rerank`).

- `scifact`, `nfcorpus`, `arguana`: `--batch-size 8`
- `scidocs`, `fiqa`: `--batch-size 16`

## Summary (NDCG@10)

| Dataset | Docs | Queries | `dense` | `lexical` | `hybrid_rrf` | `hybrid_rrf_rerank` | Best Strategy |
|---|---:|---:|---:|---:|---:|---:|---|
| SciFact | 5,183 | 300 | **0.8054** | 0.5694 | 0.7312 | 0.7492 | `dense` |
| NFCorpus | 3,633 | 323 | 0.3209 | 0.2720 | **0.3279** | 0.3259 | `hybrid_rrf` |
| ArguAna | 8,674 | 1,406 | 0.3850 | 0.3227 | **0.3882** | 0.3532 | `hybrid_rrf` |
| SciDocs | 25,657 | 1,000 | 0.1481 | 0.1424 | 0.1680 | **0.1703** | `hybrid_rrf_rerank` |
| FiQA | 57,638 | 648 | 0.3014 | 0.1631 | 0.2925 | **0.3322** | `hybrid_rrf_rerank` |

## Detailed Metrics (`scidocs` & `fiqa`)

### SciDocs (`25,657` docs, `1,000` queries)
- `dense`: NDCG@10=`0.1481`, Recall@10=`0.1596`, MRR@10=`0.2601`, p50=`76.98 ms`, p95=`121.45 ms`
- `lexical`: NDCG@10=`0.1424`, Recall@10=`0.1465`, MRR@10=`0.2581`, p50=`1.09 ms`, p95=`3.15 ms`
- `hybrid_rrf`: NDCG@10=`0.1680`, Recall@10=`0.1788`, MRR@10=`0.2908`, p50=`0.09 ms`, p95=`0.12 ms`
- `hybrid_graph_1hop`: NDCG@10=`0.1680`, Recall@10=`0.1788`, MRR@10=`0.2908`, p50=`0.04 ms`, p95=`0.05 ms`
- `hybrid_graph_2hop`: NDCG@10=`0.1680`, Recall@10=`0.1788`, MRR@10=`0.2908`, p50=`0.02 ms`, p95=`0.03 ms`
- `hybrid_rrf_rerank`: NDCG@10=`0.1703`, Recall@10=`0.1788`, MRR@10=`0.2992`, p50=`236.72 ms`, p95=`303.25 ms`

### FiQA (`57,638` docs, `648` queries)
- `dense`: NDCG@10=`0.3014`, Recall@10=`0.3796`, MRR@10=`0.3631`, p50=`104.77 ms`, p95=`163.50 ms`
- `lexical`: NDCG@10=`0.1631`, Recall@10=`0.2101`, MRR@10=`0.2044`, p50=`2.67 ms`, p95=`6.48 ms`
- `hybrid_rrf`: NDCG@10=`0.2925`, Recall@10=`0.3750`, MRR@10=`0.3539`, p50=`0.10 ms`, p95=`0.13 ms`
- `hybrid_graph_1hop`: NDCG@10=`0.2925`, Recall@10=`0.3750`, MRR@10=`0.3539`, p50=`0.03 ms`, p95=`0.04 ms`
- `hybrid_graph_2hop`: NDCG@10=`0.2925`, Recall@10=`0.3750`, MRR@10=`0.3539`, p50=`0.02 ms`, p95=`0.03 ms`
- `hybrid_rrf_rerank`: NDCG@10=`0.3322`, Recall@10=`0.3750`, MRR@10=`0.4243`, p50=`271.68 ms`, p95=`306.64 ms`
