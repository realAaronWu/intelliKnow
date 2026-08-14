from __future__ import annotations

import json
from pathlib import Path

import yaml
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.admin.service import AdminService
from app.channels.store import ChannelStore
from app.config_service import ConfigService
from app.db import create_engine_for, documents, init_schema, query_log
from app.ingest.worker import IngestDeps
from app.main import create_app
from app.orchestrator.errors import ClassificationError
from app.rag.index_writer import IndexWriter
from app.rag.vector_store import VectorStore
from tests.doubles import FakeEmbeddingProvider, FakeLLMProvider


def _setup(tmp_path: Path, *, intent_validator=None):
    config_path = tmp_path / "config.yaml"
    raw = yaml.safe_load(Path("config.yaml").read_text())
    raw["embedding"]["dimension"] = 8
    raw["storage"] = {
        "sqlite_path": str(tmp_path / "db.sqlite"),
        "faiss_dir": str(tmp_path / "faiss"),
        "upload_dir": str(tmp_path / "uploads"),
    }
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    config_service = ConfigService.load(config_path)
    engine = create_engine_for(tmp_path / "db.sqlite")
    init_schema(engine)
    vector_store = VectorStore(tmp_path / "faiss", 8)
    embedder = FakeEmbeddingProvider(dimension=8)
    deps = IngestDeps(
        engine=engine,
        cfg=config_service.current,
        classify_llm=FakeLLMProvider(),
        embedding=embedder,
        vector_store=vector_store,
        index_writer=IndexWriter(engine, vector_store, embedder),
        get_cfg=lambda: config_service.current,
    )
    channel_store = ChannelStore(
        engine,
        Fernet.generate_key().decode("ascii"),
    )
    channel_store.initialize("telegram", enabled=False)
    channel_store.initialize("teams", enabled=False)
    service = AdminService(
        engine,
        config_service,
        vector_store,
        channel_store,
        intent_validator=intent_validator,
    )
    app = create_app(deps, admin_password="secret", admin_service=service)
    return TestClient(app, headers={"Authorization": "Bearer secret"}), service, engine


def _insert_doc(engine, *, slug="hr", name="handbook.pdf") -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(documents).values(
                filename=name,
                ext=".pdf",
                size_bytes=100,
                sha256=name.ljust(64, "x")[:64],
                intent_slug=slug,
                intent_assigned_by="model",
                status="indexed",
                error_message=None,
                chunk_count=2,
                uploaded_at="2026-08-10T00:00:00Z",
                indexed_at="2026-08-10T00:00:01Z",
            )
        )
        return result.inserted_primary_key[0]


def _insert_query(
    engine,
    *,
    doc_id=None,
    intent="hr",
    correct=None,
    confidence=0.91,
    best_relevance=0.82,
    status="success",
    latency_ms=220,
    channel="telegram",
) -> int:
    snapshots = [] if doc_id is None else [{"document_id": doc_id, "document_title": "handbook.pdf"}]
    with engine.begin() as conn:
        result = conn.execute(
            insert(query_log).values(
                created_at="2026-08-11T12:00:00Z",
                channel=channel,
                user_ref="u1",
                question="How much leave?",
                intent_slug=intent,
                confidence=confidence,
                classified_by="centroid",
                fallback_used=False,
                status=status,
                answer="Twenty days. [1]",
                citations_json=json.dumps([]),
                retrieved_doc_ids_json=json.dumps([] if doc_id is None else [doc_id]),
                retrieved_documents_json=json.dumps(snapshots),
                latency_ms=latency_ms,
                best_relevance=best_relevance,
                timings_json=json.dumps({"generation": 180, "pipeline_total": 210}),
                expected_intent_slug=intent if correct is not None else None,
                reviewed_correct=correct,
                reviewed_at="2026-08-11T12:01:00Z" if correct is not None else None,
            )
        )
        return result.inserted_primary_key[0]


def test_admin_router_is_authenticated_and_dashboard_handles_empty_data(tmp_path):
    client, _, _ = _setup(tmp_path)
    assert TestClient(client.app).get("/admin/dashboard").status_code == 401
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert response.json()["document_count"] == 0
    assert len(response.json()["integrations"]) == 2


def test_query_detail_returns_stage_latency_breakdown(tmp_path):
    client, _, engine = _setup(tmp_path)
    query_id = _insert_query(engine)

    response = client.get(f"/admin/queries/{query_id}")

    assert response.status_code == 200
    assert response.json()["timings_ms"] == {
        "generation": 180,
        "pipeline_total": 210,
    }
    assert response.json()["best_relevance"] == 0.82


def test_intent_crud_protects_general_and_space_with_documents(tmp_path):
    client, _, engine = _setup(tmp_path)
    created = client.post(
        "/admin/intents",
        json={"name": "Customer Success", "description": "Account help", "keywords": ["renewal"]},
    )
    assert created.status_code == 201
    assert created.json()["slug"] == "customer-success"
    assert (tmp_path / "faiss" / "customer-success.index").exists()

    edited = client.put(
        "/admin/intents/customer-success",
        json={"name": "Client Success", "description": "Account renewals", "keywords": ["renewal", "account"]},
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "Client Success"

    assert client.delete("/admin/intents/general").status_code == 400
    _insert_doc(engine, slug="customer-success")
    blocked = client.delete("/admin/intents/customer-success")
    assert blocked.status_code == 400
    assert "1 assigned document" in blocked.json()["detail"]


def test_explicit_intent_slug_is_normalized_for_nontechnical_admins(tmp_path):
    client, _, _ = _setup(tmp_path)

    response = client.post(
        "/admin/intents",
        json={
            "name": "Tech Support",
            "slug": "Tech Support",
            "description": "Internal technology support and infrastructure.",
            "keywords": ["GPU", "network"],
        },
    )

    assert response.status_code == 201
    assert response.json()["slug"] == "tech-support"


def test_intent_slug_without_letters_or_numbers_has_actionable_error(tmp_path):
    client, _, _ = _setup(tmp_path)

    response = client.post(
        "/admin/intents",
        json={"name": "Tech", "slug": "---", "description": "Technology docs"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Slug must contain at least one letter or number, for example 'tech' "
        "or 'it-support'."
    )


def test_intent_classifier_preflight_failure_returns_503_and_saves_nothing(tmp_path):
    def unavailable(_cfg):
        raise ClassificationError("Classification service is unavailable; please retry.")

    client, service, _ = _setup(tmp_path, intent_validator=unavailable)
    config_path = tmp_path / "config.yaml"
    before = config_path.read_bytes()

    response = client.post(
        "/admin/intents",
        json={"name": "Safety", "description": "Safety policy", "keywords": []},
    )

    assert response.status_code == 503
    assert "retry" in response.json()["detail"].lower()
    assert config_path.read_bytes() == before
    assert all(space.slug != "safety" for space in service.config.intent_spaces)
    assert not (tmp_path / "faiss" / "safety.index").exists()


def test_runtime_thresholds_update_and_restart_only_fields_are_not_exposed(tmp_path):
    client, _, _ = _setup(tmp_path)
    response = client.patch(
        "/admin/config",
        json={"confidence_threshold": 0.82, "relevance_floor": 0.51},
    )
    assert response.status_code == 200
    assert response.json()["orchestrator"]["confidence_threshold"] == 0.82
    assert "storage" not in response.json()


def test_query_history_feedback_accuracy_and_detail(tmp_path):
    client, _, engine = _setup(tmp_path)
    query_id = _insert_query(engine)

    page = client.get("/admin/queries", params={"limit": 1})
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["id"] == query_id
    detail = client.get(f"/admin/queries/{query_id}").json()
    assert detail["answer"] == "Twenty days. [1]"

    reviewed = client.put(
        f"/admin/queries/{query_id}/review",
        json={"expected_intent_slug": "finance"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed_correct"] is False
    analytics = client.get("/admin/analytics").json()
    assert analytics["reviewed_accuracy"] == {
        "available": True,
        "reviewed": 1,
        "correct": 0,
        "value": 0.0,
    }
    assert analytics["high_confidence_share"] == 1.0
    assert analytics["latency_gate_by_channel"] == {
        "telegram": {
            "count": 1,
            "target_ms": 3000,
            "p50_ms": 220,
            "p95_ms": 220,
            "max_ms": 220,
            "pass_rate": 1.0,
            "passed": True,
        }
    }


def test_analytics_reports_per_channel_p95_latency_gate(tmp_path):
    client, _, engine = _setup(tmp_path)
    for latency in (900, 1200, 2400, 3100):
        _insert_query(engine, latency_ms=latency)
    _insert_query(engine, latency_ms=800, channel="admin")

    metrics = client.get("/admin/analytics").json()["latency_gate_by_channel"]

    assert metrics["telegram"] == {
        "count": 4,
        "target_ms": 3000,
        "p50_ms": 1200,
        "p95_ms": 3100,
        "max_ms": 3100,
        "pass_rate": 0.75,
        "passed": False,
    }
    assert "admin" not in metrics


def test_usage_snapshot_survives_document_deletion_and_empty_csv_has_headers(tmp_path):
    client, _, engine = _setup(tmp_path)
    empty = client.get("/admin/analytics/export")
    assert empty.status_code == 200
    assert empty.text.startswith("id,created_at,channel")
    assert len(empty.text.strip().splitlines()) == 1

    doc_id = _insert_doc(engine)
    _insert_query(engine, doc_id=doc_id)
    with engine.begin() as conn:
        conn.execute(documents.delete().where(documents.c.id == doc_id))
    usage = client.get("/admin/analytics").json()["most_accessed_documents"]
    assert usage == [{"document_id": doc_id, "document_title": "handbook.pdf", "access_count": 1}]


def test_recent_channel_errors_are_retained_not_only_the_latest(tmp_path):
    client, service, _ = _setup(tmp_path)
    service.channel_store.record_error("telegram", "first")
    service.channel_store.record_error("telegram", "second")
    errors = service.channel_store.recent_errors("telegram")
    assert [item["reason"] for item in errors] == ["second", "first"]
