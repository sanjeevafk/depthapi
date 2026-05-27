from typing import List, Dict, Any
try:
    from langchain_community.vectorstores import FAISS
except Exception:
    FAISS = None
try:
    from langchain_huggingface import HuggingFaceEmbeddings
except Exception:
    HuggingFaceEmbeddings = None
from langchain_groq import ChatGroq
from corpus_supabase import rest_rpc

# Minimal local PromptTemplate and Document to avoid langchain_core dependency
class PromptTemplate:
    def __init__(self, template: str, input_variables=None):
        self.template = template
        self.input_variables = input_variables or []

    def format(self, **kwargs):
        return self.template.format(**kwargs)

class Document:
    def __init__(self, page_content: str, metadata: dict = None):
        self.page_content = page_content
        self.metadata = metadata or {}
import asyncio
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

class LangChainBaseline:
    """A basic LangChain RAG baseline to compare against DepthAPI."""
    
    def __init__(self, docs: List[Dict[str, Any]]):
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            print("Warning: GROQ_API_KEY not set. LangChainBaseline LLM will fail.")
            api_key = "dummy_key_to_allow_init"
            
        if HuggingFaceEmbeddings is not None:
            self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        else:
            # Fallback simple embeddings to allow offline runs
            class SimpleEmbeddings:
                def embed_documents(self, texts: List[str]):
                    return [[0.1] * 384 for _ in texts]
                def embed_query(self, text: str):
                    return [0.1] * 384
            self.embeddings = SimpleEmbeddings()
        self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0, api_key=api_key, max_retries=5)
        self.vectorstore = None
        self._initialize(docs)
        
        prompt_template = """Use the following pieces of context to answer the question at the end. 
If you don't know the answer, just say that you don't know, don't try to make up an answer.

Context:
{context}

Question: {question}
Answer:"""
        self.prompt = PromptTemplate(
            template=prompt_template, input_variables=["context", "question"]
        )
        
    def _initialize(self, docs: List[Dict[str, Any]]):
        """Initialize the vector store."""
        documents = [
            Document(page_content=d["content"], metadata=d.get("metadata", {}))
            for d in docs
        ]
        if not documents:
            documents = [Document(page_content="Dummy document to initialize vectorstore", metadata={})]
        # If FAISS is unavailable, use a simple in-memory vector store fallback
        if FAISS is not None:
            ids = [str(i) for i in range(len(documents))]
            self.vectorstore = FAISS.from_documents(documents, self.embeddings, ids=ids)
        else:
            # Simple in-memory vectorstore implementing similarity_search(query, k)
            class SimpleVectorStore:
                def __init__(self, docs, embedder):
                    self.docs = docs
                    self.embedder = embedder
                    try:
                        # cache embeddings
                        self._embs = [embedder.embed_documents([d.page_content])[0] for d in docs]
                    except Exception:
                        # fallback: zero vectors
                        self._embs = [[0.0]*384 for _ in docs]

                def similarity_search(self, query, k=4):
                    qv = self.embedder.embed_query(query)
                    # compute simple dot product scores
                    scores = []
                    for i, ev in enumerate(self._embs):
                        try:
                            s = sum(a*b for a,b in zip(ev, qv))
                        except Exception:
                            s = 0.0
                        scores.append((s, self.docs[i]))
                    scores.sort(key=lambda x: x[0], reverse=True)
                    return [d for _, d in scores[:k]]

            self.vectorstore = SimpleVectorStore(documents, self.embeddings)
        
    async def query(self, query: str) -> Dict[str, Any]:
        """Query the LangChain baseline asynchronously."""
        loop = asyncio.get_event_loop()
        try:
            # Run similarity search in executor since it might block
            source_docs = await loop.run_in_executor(
                None, 
                lambda: self.vectorstore.similarity_search(query, k=4)
            )
            
            context_str = "\n\n".join([doc.page_content for doc in source_docs])
            prompt_str = self.prompt.format(context=context_str, question=query)
            
            # Invoke LLM asynchronously
            res = await self.llm.ainvoke(prompt_str)
            
            # ChatGroq ainvoke returns an AIMessage, get content
            answer = res.content if hasattr(res, "content") else str(res)
            
            return {
                "answer": answer, 
                "context": [doc.page_content for doc in source_docs]
            }
        except Exception as e:
            return {"error": str(e), "answer": "Error in LangChain baseline", "context": []}


class SupabaseCorpusBaseline:
    """Baseline RAG over the same local trusted corpus as DepthAPI."""

    def __init__(self, docs: List[Dict[str, Any]] | None = None):
        self.llm = ChatGroq(model=os.environ.get("EVALUATOR_MODEL", "llama-3.3-70b-versatile"), temperature=0, max_retries=3)
        self.use_llm = (os.environ.get("BASELINE_USE_LLM", "0") == "1")

    async def _embed(self, query: str) -> list[float]:
        try:
            from api.services.rag.embeddings import get_embedding_service
            vectors = await get_embedding_service().create_embeddings([query])
            return vectors[0]
        except ImportError:
            if HuggingFaceEmbeddings is not None:
                import asyncio
                embedder = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
                # run embed_query in thread since it's synchronous
                return await asyncio.to_thread(embedder.embed_query, query)
            return [0.1] * 384

    async def query(self, query: str) -> Dict[str, Any]:
        try:
            query_embedding = await self._embed(query)
            res = rest_rpc("hybrid_search_trusted_v5", {
                "query_text": query,
                "query_embedding": query_embedding,
                "query_mode": "conceptual",
                "candidate_pool_size": 100,
                "final_count": int(os.getenv("RAG_TOP_K", "5")),
                "min_similarity": float(os.getenv("RAG_MIN_SIMILARITY", "0.65")),
            })
            res.raise_for_status()
            rows = res.json() or []
            contexts = [
                {
                    "doc_id": row.get("document_id"),
                    "chunk_id": row.get("chunk_id"),
                    "text": row.get("content", ""),
                    "score": row.get("rrf_score"),
                    "vector_similarity": row.get("vector_similarity"),
                    "match_source": row.get("match_source"),
                    "source": row.get("source_url") or row.get("filename"),
                    "metadata": row.get("metadata") or {},
                }
                for row in rows
            ]
            if self.use_llm:
                context_text = "\n\n".join(f"[{i+1}] {ctx['text']}" for i, ctx in enumerate(contexts))
                prompt = (
                    "Answer using only the retrieved corpus context. Cite sources by bracket number.\n\n"
                    f"Question: {query}\n\nContext:\n{context_text}\n\nAnswer:"
                )
                answer = (await self.llm.ainvoke(prompt)).content
            else:
                snippets = [ctx["text"].strip() for ctx in contexts[:2] if isinstance(ctx.get("text"), str)]
                if snippets:
                    answer = " ".join(snippets)
                else:
                    answer = "I could not find enough relevant context in the corpus."
            return {
                "answer": answer,
                "context": [ctx["text"] for ctx in contexts],
                "contexts": contexts,
                "citations": [
                    {
                        "doc_id": ctx.get("doc_id"),
                        "chunk_id": ctx.get("chunk_id"),
                        "source": ctx.get("source"),
                        "score": ctx.get("score"),
                    }
                    for ctx in contexts
                ],
            }
        except Exception as e:
            return {"error": str(e), "answer": "Error in LangChain baseline", "context": [], "contexts": []}
