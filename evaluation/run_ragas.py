import sys
from types import ModuleType

# Mock langchain_community.chat_models.vertexai
try:
    import langchain_community.chat_models.vertexai
except ModuleNotFoundError:
    mock_mod = ModuleType("langchain_community.chat_models.vertexai")
    mock_mod.ChatVertexAI = None
    sys.modules["langchain_community.chat_models.vertexai"] = mock_mod

# Mock langchain_community.llms (or its VertexAI part)
try:
    import langchain_community.llms as lc_llms
    if not hasattr(lc_llms, "VertexAI"):
        lc_llms.VertexAI = None
except ModuleNotFoundError:
    mock_mod = ModuleType("langchain_community.llms")
    mock_mod.VertexAI = None
    sys.modules["langchain_community.llms"] = mock_mod

from typing import Dict, Any, List
import os

def evaluate_ragas(query: str, answer: str, context: List[str]) -> Dict[str, Any]:
    """
    Run Ragas metrics.
    Requires ragas package. Configures custom ChatGroq LLM and embeddings to bypass OpenAI.
    """
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, faithfulness
        from langchain_groq import ChatGroq
        from ragas.llms import LangchainLLMWrapper as LangchainLLM
        from ragas.embeddings import LangchainEmbeddingsWrapper as LangchainEmbeddings
        
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return {"ragas_answer_relevancy": 0.0, "ragas_faithfulness": 0.0, "error": "GROQ_API_KEY not set"}
            
        llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0, api_key=api_key, max_retries=5)
        ragas_llm = LangchainLLM(langchain_llm=llm)
        
        # Setup embeddings
        try:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        except Exception:
            # Fallback if sentence-transformers or langchain-community is missing
            from langchain_core.embeddings import Embeddings
            class SimpleEmbeddings(Embeddings):
                def embed_documents(self, texts: List[str]) -> List[List[float]]:
                    return [[0.1] * 384 for _ in texts]
                def embed_query(self, text: str) -> List[float]:
                    return [0.1] * 384
            embeddings = SimpleEmbeddings()
            
        ragas_embeddings = LangchainEmbeddings(embeddings=embeddings)
        
        # Bind the custom LLM/embeddings to the metrics
        answer_relevancy.llm = ragas_llm
        answer_relevancy.embeddings = ragas_embeddings
        faithfulness.llm = ragas_llm
        
        data = {
            "question": [query],
            "answer": [answer],
            "contexts": [context]
        }
        dataset = Dataset.from_dict(data)
        
        result = evaluate(
            dataset,
            metrics=[answer_relevancy, faithfulness],
            raise_exceptions=False
        )
        
        # result is an EvaluationResult object, we need to extract scores from its internal scores list or _scores_dict
        scores_dict = getattr(result, "_scores_dict", {})
        
        return {
            "ragas_answer_relevancy": scores_dict.get("answer_relevancy", [0.0])[0] if "answer_relevancy" in scores_dict else 0.0,
            "ragas_faithfulness": scores_dict.get("faithfulness", [0.0])[0] if "faithfulness" in scores_dict else 0.0
        }
    except ImportError as e:
        return {"ragas_answer_relevancy": 0.0, "ragas_faithfulness": 0.0, "error": f"Import error: {str(e)}"}
    except Exception as e:
        return {"ragas_answer_relevancy": 0.0, "ragas_faithfulness": 0.0, "error": str(e)}
