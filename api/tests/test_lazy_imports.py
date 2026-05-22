import builtins
import importlib
import sys


def test_embeddings_module_import_does_not_require_sentence_transformers(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sentence_transformers":
            raise ModuleNotFoundError("blocked for test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("api.services.rag.embeddings", None)
    module = importlib.import_module("api.services.rag.embeddings")
    assert module is not None


def test_filesystem_rag_store_module_import_does_not_require_faiss(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "faiss":
            raise ModuleNotFoundError("blocked for test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("api.services.rag.filesystem_rag_store", None)
    module = importlib.import_module("api.services.rag.filesystem_rag_store")
    assert module is not None
