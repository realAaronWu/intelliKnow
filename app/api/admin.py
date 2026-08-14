"""Authenticated administration API used by the Streamlit console."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.admin.service import AdminService
from app.orchestrator.errors import ClassificationError


class IntentRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    slug: str | None = None


class RuntimeConfigRequest(BaseModel):
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    relevance_floor: float = Field(ge=0.0, le=1.0)


class ReviewRequest(BaseModel):
    expected_intent_slug: str


def _not_found(exc: LookupError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _retryable(exc: ClassificationError) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


def build_admin_router(service: AdminService) -> APIRouter:
    router = APIRouter(prefix="/admin")

    @router.get("/dashboard")
    def dashboard() -> dict:
        return service.dashboard()

    @router.get("/config")
    def config() -> dict:
        return service.config_summary()

    @router.patch("/config")
    def update_config(body: RuntimeConfigRequest) -> dict:
        try:
            return service.update_runtime(
                {
                    "orchestrator": {
                        "confidence_threshold": body.confidence_threshold
                    },
                    "rag": {"relevance_floor": body.relevance_floor},
                }
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/intents")
    def list_intents(days: int = Query(default=30, ge=1, le=3650)) -> list[dict]:
        return service.list_intents(days=days)

    @router.post("/intents", status_code=201)
    def create_intent(body: IntentRequest) -> dict:
        try:
            return service.create_intent(body.model_dump(exclude_none=True))
        except ClassificationError as exc:
            raise _retryable(exc) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.put("/intents/{slug}")
    def update_intent(slug: str, body: IntentRequest) -> dict:
        try:
            return service.update_intent(slug, body.model_dump(exclude_none=True))
        except ClassificationError as exc:
            raise _retryable(exc) from exc
        except LookupError as exc:
            raise _not_found(exc) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.delete("/intents/{slug}", status_code=204)
    def delete_intent(slug: str) -> None:
        try:
            service.delete_intent(slug)
        except ClassificationError as exc:
            raise _retryable(exc) from exc
        except LookupError as exc:
            raise _not_found(exc) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/queries")
    def queries(
        limit: int = Query(default=25, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        intent_slug: str | None = None,
        status: str | None = None,
        channel: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        return service.list_queries(
            limit=limit,
            offset=offset,
            intent_slug=intent_slug,
            status=status,
            channel=channel,
            date_from=date_from,
            date_to=date_to,
        )

    @router.get("/queries/{query_id}")
    def query_detail(query_id: int) -> dict:
        try:
            return service.get_query(query_id)
        except LookupError as exc:
            raise _not_found(exc) from exc

    @router.put("/queries/{query_id}/review")
    def review_query(query_id: int, body: ReviewRequest) -> dict:
        try:
            return service.review_query(query_id, body.expected_intent_slug)
        except LookupError as exc:
            raise _not_found(exc) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/analytics")
    def analytics(date_from: str | None = None, date_to: str | None = None) -> dict:
        return service.analytics(date_from=date_from, date_to=date_to)

    @router.get("/analytics/export")
    def export(date_from: str | None = None, date_to: str | None = None) -> Response:
        return Response(
            service.export_csv(date_from=date_from, date_to=date_to),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="intelliknow-queries.csv"'},
        )

    return router
