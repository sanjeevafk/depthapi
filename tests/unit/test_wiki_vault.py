"""
Unit tests for Karpathy LLM-Wiki Vault Manager, Rust linter integration, and API router.
"""
from __future__ import annotations

import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key
from api.services.wiki.vault_manager import WikiVaultManager


@pytest.fixture
def temp_vault():
    temp_dir = tempfile.mkdtemp(prefix="test_vault_")
    vault = WikiVaultManager(temp_dir)
    yield vault
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_vault_export_and_materialization(temp_vault: WikiVaultManager):
    concepts = [
        {
            "id": "c1",
            "name": "FastAPI",
            "concept_type": "framework",
            "description": "Fast modern Python web framework.",
            "metadata": {"version": "0.100.0"},
        },
        {
            "id": "c2",
            "name": "Pydantic",
            "concept_type": "library",
            "description": "Data validation using Python type annotations.",
            "metadata": {},
        },
    ]
    edges = [
        {
            "source_concept_id": "c1",
            "target_concept_id": "c2",
            "relation_type": "uses",
        }
    ]

    result = temp_vault.export_concepts_to_vault(concepts, edges)
    assert result["status"] == "ok"
    assert result["exported_count"] == 2

    # Verify concept files exist
    fastapi_file = temp_vault.concepts_dir / "fastapi.md"
    assert fastapi_file.exists()
    content = fastapi_file.read_text(encoding="utf-8")
    assert "# FastAPI" in content
    assert "[[Pydantic]]" in content

    # Verify index.md
    index_file = temp_vault.vault_dir / "index.md"
    assert index_file.exists()
    index_content = index_file.read_text(encoding="utf-8")
    assert "[[FastAPI]]" in index_content
    assert "[[Pydantic]]" in index_content

    # Verify log.md
    log_file = temp_vault.vault_dir / "log.md"
    assert log_file.exists()
    assert "Vault Materialization" in log_file.read_text(encoding="utf-8")


def test_vault_save_qa_insight(temp_vault: WikiVaultManager):
    res = temp_vault.save_qa_insight(
        query="What is DepthAPI?",
        answer="DepthAPI is an open cognitive synthesis engine combining Rust retrieval with Karpathy LLM-Wiki.",
        referenced_concepts=["FastAPI", "Rust"],
    )
    assert res["status"] == "saved"

    synthesis_file = temp_vault.concepts_dir / f"{res['slug']}.md"
    assert synthesis_file.exists()
    content = synthesis_file.read_text(encoding="utf-8")
    assert "## Question\nWhat is DepthAPI?" in content
    assert "[[FastAPI]]" in content
    assert "[[Rust]]" in content

    # Check log.md recorded the Q&A insight
    log_file = temp_vault.vault_dir / "log.md"
    assert "Q&A Insight Saved" in log_file.read_text(encoding="utf-8")


def test_vault_linter_clean_vault(temp_vault: WikiVaultManager):
    concepts = [
        {"name": "Alpha", "concept_type": "topic", "description": "Concept Alpha"},
        {"name": "Beta", "concept_type": "topic", "description": "Concept Beta"},
    ]
    edges = [
        {"source_concept": "Alpha", "target_concept": "Beta", "relation_type": "relates_to"}
    ]
    temp_vault.export_concepts_to_vault(concepts, edges)

    report = temp_vault.lint_vault()
    assert report["valid"] is True
    assert report["broken_links"] == []
    assert report["cycles"] == []
    # Both Alpha and Beta are linked from index.md, so no orphans
    assert report["orphan_nodes"] == []


def test_vault_linter_detects_broken_links(temp_vault: WikiVaultManager):
    # Manually write a note with a broken link
    broken_note = temp_vault.concepts_dir / "broken.md"
    broken_note.write_text("# Broken Note\n\nLinks to [[NonExistentConcept]].\n", encoding="utf-8")

    report = temp_vault.lint_vault()
    assert report["valid"] is False
    assert len(report["broken_links"]) >= 1
    assert any(b["target"] == "NonExistentConcept" for b in report["broken_links"])


def test_vault_linter_detects_cycles(temp_vault: WikiVaultManager):
    # Create directed cycle between NodeA and NodeB
    (temp_vault.concepts_dir / "node_a.md").write_text("# Node A\n\nLinks to [[node_b]].\n", encoding="utf-8")
    (temp_vault.concepts_dir / "node_b.md").write_text("# Node B\n\nLinks to [[node_a]].\n", encoding="utf-8")

    report = temp_vault.lint_vault()
    assert report["valid"] is False
    assert len(report["cycles"]) >= 1


def test_wiki_router_endpoints(monkeypatch, temp_vault: WikiVaultManager):
    fake_key = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="pro", is_pro=True)
    app.dependency_overrides[verify_api_key] = lambda: fake_key

    monkeypatch.setattr("api.routers.wiki.get_vault_manager", lambda: temp_vault)

    client = TestClient(app)

    # Test export endpoint
    res_export = client.post("/api/wiki/export", json={}, headers={"Authorization": "Bearer test"})
    assert res_export.status_code == 200
    export_data = res_export.json()
    assert export_data["status"] == "ok"

    # Test lint endpoint
    res_lint = client.get("/api/wiki/lint")
    assert res_lint.status_code == 200
    assert "total_notes" in res_lint.json()

    # Add a concept and test list & read
    temp_vault.export_concepts_to_vault([{"name": "RustLang", "concept_type": "topic", "description": "Fast engine"}])

    res_list = client.get("/api/wiki/concepts")
    assert res_list.status_code == 200
    assert any(c["slug"] == "rustlang" for c in res_list.json())

    res_read = client.get("/api/wiki/concepts/rustlang")
    assert res_read.status_code == 200
    assert "RustLang" in res_read.json()["content"]

    res_404 = client.get("/api/wiki/concepts/nonexistent")
    assert res_404.status_code == 404

    app.dependency_overrides.clear()
