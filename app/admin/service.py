"""Small query/configuration service behind the authenticated admin API."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import Engine, func, select

from app.channels.store import ChannelStore
from app.config import AppConfig, IntentSpace
from app.config_service import ConfigService
from app.db import chunks, documents, query_log
from app.rag.vector_store import VectorStore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


class AdminService:
    def __init__(
        self,
        engine: Engine,
        config_service: ConfigService,
        vector_store: VectorStore,
        channel_store: ChannelStore,
        intent_validator: Callable[[AppConfig], None] | None = None,
    ) -> None:
        self.engine = engine
        self.config_service = config_service
        self.vector_store = vector_store
        self.channel_store = channel_store
        self._intent_validator = intent_validator

    @property
    def config(self):
        return self.config_service.current

    def config_summary(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "llm": {
                "provider": cfg.llm.provider,
                "model_classify": cfg.llm.model_classify,
                "model_generate": cfg.llm.model_generate,
            },
            "embedding": {
                "provider": cfg.embedding.provider,
                "model": cfg.embedding.model,
            },
            "orchestrator": {
                "confidence_threshold": cfg.orchestrator.confidence_threshold,
                "fallback_space": cfg.orchestrator.fallback_space,
            },
            "rag": {"relevance_floor": cfg.rag.relevance_floor},
            "ingestion": {
                "allowed_extensions": cfg.ingestion.allowed_extensions,
                "max_upload_mb": cfg.ingestion.max_upload_mb,
            },
        }

    def update_runtime(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.config_service.update_runtime(patch)
        return self.config_summary()

    def _accuracy_by_space(self, date_from: str | None = None) -> dict[str, dict]:
        stmt = (
            select(
                query_log.c.intent_slug,
                func.count(query_log.c.id).label("reviewed"),
                func.sum(func.coalesce(query_log.c.reviewed_correct, 0)).label("correct"),
            )
            .where(query_log.c.reviewed_correct.is_not(None))
            .group_by(query_log.c.intent_slug)
        )
        if date_from:
            stmt = stmt.where(query_log.c.created_at >= date_from)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return {
            row.intent_slug: {
                "reviewed": row.reviewed,
                "correct": int(row.correct or 0),
                "accuracy": (int(row.correct or 0) / row.reviewed) if row.reviewed else None,
            }
            for row in rows
            if row.intent_slug
        }

    def list_intents(self, *, days: int = 30) -> list[dict[str, Any]]:
        date_from = (
            datetime.now(timezone.utc) - timedelta(days=max(1, days))
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.engine.connect() as conn:
            counts = dict(
                conn.execute(
                    select(documents.c.intent_slug, func.count(documents.c.id)).group_by(
                        documents.c.intent_slug
                    )
                ).all()
            )
        accuracy = self._accuracy_by_space(date_from)
        return [
            {
                **space.model_dump(mode="json"),
                "document_count": int(counts.get(space.slug, 0)),
                "protected": space.slug == self.config.orchestrator.fallback_space,
                "reviewed_accuracy": accuracy.get(space.slug),
            }
            for space in self.config.intent_spaces
        ]

    def create_intent(self, data: dict[str, Any]) -> dict[str, Any]:
        slug = _slugify(data.get("slug") or data.get("name", ""))
        if not slug:
            raise ValueError(
                "Slug must contain at least one letter or number, for example 'tech' "
                "or 'it-support'."
            )
        space = IntentSpace.model_validate({**data, "slug": slug})
        spaces = [item.model_dump(mode="json") for item in self.config.intent_spaces]
        if any(item["slug"] == slug for item in spaces):
            raise ValueError(f"intent space {slug!r} already exists")
        self._save_intents([*spaces, space.model_dump(mode="json")])
        self.vector_store.create_space(slug)
        self.vector_store.persist(slug)
        return next(item for item in self.list_intents() if item["slug"] == slug)

    def update_intent(self, slug: str, data: dict[str, Any]) -> dict[str, Any]:
        spaces = [item.model_dump(mode="json") for item in self.config.intent_spaces]
        index = next((i for i, item in enumerate(spaces) if item["slug"] == slug), None)
        if index is None:
            raise LookupError(f"intent space {slug!r} not found")
        if data.get("slug", slug) != slug:
            raise ValueError("an intent space slug cannot be changed")
        spaces[index] = IntentSpace.model_validate(
            {**spaces[index], **data, "slug": slug}
        ).model_dump(mode="json")
        self._save_intents(spaces)
        return next(item for item in self.list_intents() if item["slug"] == slug)

    def delete_intent(self, slug: str) -> None:
        cfg = self.config
        if slug == cfg.orchestrator.fallback_space:
            raise ValueError(f"{slug!r} is the required protected space and cannot be deleted")
        spaces = [item.model_dump(mode="json") for item in cfg.intent_spaces]
        if not any(item["slug"] == slug for item in spaces):
            raise LookupError(f"intent space {slug!r} not found")
        with self.engine.connect() as conn:
            count = conn.execute(
                select(func.count(documents.c.id)).where(documents.c.intent_slug == slug)
            ).scalar_one()
        if count:
            raise ValueError(
                f"intent space {slug!r} has {count} assigned document(s); reassign them first"
            )
        self._save_intents([item for item in spaces if item["slug"] != slug])
        self.vector_store.delete_space(slug)

    def _save_intents(self, spaces: list[dict[str, Any]]) -> None:
        patch = {"intent_spaces": spaces}
        proposed = self.config_service.preview_runtime(patch)
        if self._intent_validator is not None:
            self._intent_validator(proposed)
        self.config_service.update_runtime(patch)

    def _query_filters(
        self,
        *,
        intent_slug: str | None,
        status: str | None,
        channel: str | None,
        date_from: str | None,
        date_to: str | None,
    ) -> list:
        filters = []
        if intent_slug:
            filters.append(query_log.c.intent_slug == intent_slug)
        if status:
            filters.append(query_log.c.status == status)
        if channel:
            filters.append(query_log.c.channel == channel)
        if date_from:
            filters.append(query_log.c.created_at >= date_from)
        if date_to:
            filters.append(query_log.c.created_at <= date_to)
        return filters

    def list_queries(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
        intent_slug: str | None = None,
        status: str | None = None,
        channel: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        filters = self._query_filters(
            intent_slug=intent_slug,
            status=status,
            channel=channel,
            date_from=date_from,
            date_to=date_to,
        )
        base = select(query_log).where(*filters)
        with self.engine.connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(query_log).where(*filters)
            ).scalar_one()
            rows = conn.execute(
                base.order_by(query_log.c.id.desc()).limit(limit).offset(offset)
            ).mappings().all()
        return {
            "items": [self._query_dict(row, detail=False) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def _query_dict(self, row, *, detail: bool) -> dict[str, Any]:
        item = {
            "id": row["id"],
            "created_at": row["created_at"],
            "channel": row["channel"],
            "question": row["question"],
            "intent_slug": row["intent_slug"],
            "confidence": row["confidence"],
            "classified_by": row["classified_by"],
            "fallback_used": bool(row["fallback_used"]),
            "status": row["status"],
            "latency_ms": row["latency_ms"],
            "expected_intent_slug": row["expected_intent_slug"],
            "reviewed_correct": row["reviewed_correct"],
            "reviewed_at": row["reviewed_at"],
        }
        if detail:
            item.update(
                answer=row["answer"],
                citations=_json_list(row["citations_json"]),
                retrieved_documents=self._retrieved_documents(row),
                reasoning=row["reasoning"],
                error=row["error"],
            )
        return item

    def _retrieved_documents(self, row) -> list[dict[str, Any]]:
        snapshots = _json_list(row["retrieved_documents_json"])
        if snapshots:
            return snapshots
        return [
            {"document_id": doc_id, "document_title": f"Document {doc_id}"}
            for doc_id in _json_list(row["retrieved_doc_ids_json"])
        ]

    def get_query(self, query_id: int) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(query_log).where(query_log.c.id == query_id)
            ).mappings().one_or_none()
        if row is None:
            raise LookupError(f"query {query_id} not found")
        return self._query_dict(row, detail=True)

    def review_query(self, query_id: int, expected_intent_slug: str) -> dict[str, Any]:
        valid = {space.slug for space in self.config.intent_spaces}
        if expected_intent_slug not in valid:
            raise ValueError(f"unknown expected intent space: {expected_intent_slug!r}")
        with self.engine.begin() as conn:
            row = conn.execute(
                select(query_log.c.intent_slug).where(query_log.c.id == query_id)
            ).one_or_none()
            if row is None:
                raise LookupError(f"query {query_id} not found")
            conn.execute(
                query_log.update()
                .where(query_log.c.id == query_id)
                .values(
                    expected_intent_slug=expected_intent_slug,
                    reviewed_correct=row.intent_slug == expected_intent_slug,
                    reviewed_at=_utc_now_iso(),
                )
            )
        return self.get_query(query_id)

    def analytics(
        self, *, date_from: str | None = None, date_to: str | None = None
    ) -> dict[str, Any]:
        filters = self._query_filters(
            intent_slug=None,
            status=None,
            channel=None,
            date_from=date_from,
            date_to=date_to,
        )
        with self.engine.connect() as conn:
            rows = conn.execute(select(query_log).where(*filters)).mappings().all()
        distribution: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        access: dict[tuple[int, str], int] = {}
        reviewed = 0
        correct = 0
        high_confidence = 0
        latency_values: list[int] = []
        threshold = self.config.orchestrator.confidence_threshold
        for row in rows:
            slug = row["intent_slug"] or "unknown"
            distribution[slug] = distribution.get(slug, 0) + 1
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
            if row["reviewed_correct"] is not None:
                reviewed += 1
                correct += int(bool(row["reviewed_correct"]))
            if row["confidence"] is not None and row["confidence"] >= threshold:
                high_confidence += 1
            if row["latency_ms"] is not None:
                latency_values.append(row["latency_ms"])
            for doc in self._retrieved_documents(row):
                key = (int(doc["document_id"]), str(doc["document_title"]))
                access[key] = access.get(key, 0) + 1
        most_accessed = [
            {"document_id": key[0], "document_title": key[1], "access_count": count}
            for key, count in sorted(access.items(), key=lambda item: (-item[1], item[0][1]))
        ]
        return {
            "query_count": len(rows),
            "intent_distribution": distribution,
            "status_counts": status_counts,
            "most_accessed_documents": most_accessed,
            "reviewed_accuracy": {
                "available": reviewed > 0,
                "reviewed": reviewed,
                "correct": correct,
                "value": correct / reviewed if reviewed else None,
            },
            "high_confidence_share": high_confidence / len(rows) if rows else None,
            "average_latency_ms": (
                round(sum(latency_values) / len(latency_values)) if latency_values else None
            ),
        }

    def dashboard(self) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self.engine.connect() as conn:
            document_count = conn.execute(select(func.count(documents.c.id))).scalar_one()
            chunk_count = conn.execute(select(func.count(chunks.c.id))).scalar_one()
            failed_documents = conn.execute(
                select(func.count(documents.c.id)).where(documents.c.status == "failed")
            ).scalar_one()
            per_space = dict(
                conn.execute(
                    select(documents.c.intent_slug, func.count(documents.c.id)).group_by(
                        documents.c.intent_slug
                    )
                ).all()
            )
            recent_queries = conn.execute(
                select(func.count(query_log.c.id)).where(query_log.c.created_at >= since)
            ).scalar_one()
        return {
            "document_count": document_count,
            "chunk_count": chunk_count,
            "failed_documents": failed_documents,
            "documents_by_intent": per_space,
            "queries_last_7_days": recent_queries,
            "integrations": [
                {
                    "channel": channel,
                    "status": self.channel_store.get(channel).status,
                    "enabled": self.channel_store.get(channel).enabled,
                    "last_error": self.channel_store.get(channel).last_error,
                }
                for channel in ("telegram", "teams")
            ],
            "config": self.config_summary(),
        }

    def export_csv(
        self, *, date_from: str | None = None, date_to: str | None = None
    ) -> str:
        filters = self._query_filters(
            intent_slug=None,
            status=None,
            channel=None,
            date_from=date_from,
            date_to=date_to,
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(query_log).where(*filters).order_by(query_log.c.id.desc())
            ).mappings().all()
        fields = [
            "id", "created_at", "channel", "question", "intent_slug", "confidence",
            "classified_by", "fallback_used", "status", "answer", "citations_json",
            "retrieved_doc_ids_json", "latency_ms", "error", "expected_intent_slug",
            "reviewed_correct", "reviewed_at",
        ]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()
