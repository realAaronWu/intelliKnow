"""FastAPI application entrypoint.

`uv run uvicorn app.main:app --port 8000` is the supported way to run the
API. Production composition requires `ADMIN_PASSWORD` and mounts every
administrative route behind the shared bearer-token dependency.

`app` is exposed lazily, via `__getattr__` (PEP 562), rather than built
eagerly at module scope. `uvicorn app.main:app` resolves `app` through
`getattr` on this module, which is the only case meant to trigger the
real `bootstrap()` composition root — opening the real SQLite database,
the real FAISS directory, and the real configured LLM/embedding
providers. A plain `from app.main import create_app` — what tests do,
building their own app from fake providers and a tmp-path database
instead — never touches the `app` attribute, so importing this module
for testing has none of those side effects.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI

from sqlalchemy.engine import Engine

from app.analytics.log import QueryLogger
from app.api.auth import build_admin_auth
from app.api.documents import build_documents_router
from app.api.integrations import build_integrations_router
from app.api.query import build_query_router
from app.bootstrap import Application, bootstrap
from app.channels.handler import ChannelHandler
from app.channels.store import ChannelStore
from app.channels.telegram import TelegramPoller
from app.channels.teams import TeamsEndpoint, build_teams_router
from app.channels.tester import ChannelTestService
from app.db import create_engine_for, init_schema, recover_interrupted_documents
from app.ingest.worker import IngestDeps
from app.ingest.classify_doc import preflight_classifier
from app.orchestrator.centroids import CentroidIndex
from app.orchestrator.pipeline import PipelineDeps, answer_question
from app.rag.index_writer import IndexWriter
from app.rag.retrieve.rerank import Reranker
from app.rag.vector_store import VectorStore


def create_app(
    deps: IngestDeps,
    pipeline_deps: PipelineDeps | None = None,
    *,
    admin_password: str | None = None,
    lifespan=None,
    teams_endpoint: TeamsEndpoint | None = None,
    integration_store: ChannelStore | None = None,
    channel_tester: ChannelTestService | None = None,
) -> FastAPI:
    """Build the FastAPI app bound to `deps` (and, when supplied,
    `pipeline_deps`). Pure: no I/O beyond constructing the
    `FastAPI`/`APIRouter` objects themselves.

    `pipeline_deps` is optional so every existing caller that only cares
    about the documents API (`tests/test_documents_api.py`, in
    particular) keeps working unchanged — the `/admin/test-query` route
    is simply absent when it is not supplied.
    """
    fastapi_app = FastAPI(title="IntelliKnow KMS", lifespan=lifespan)
    admin_dependencies = (
        [Depends(build_admin_auth(admin_password))] if admin_password is not None else []
    )
    fastapi_app.include_router(
        build_documents_router(deps), dependencies=admin_dependencies
    )
    if pipeline_deps is not None:
        fastapi_app.include_router(
            build_query_router(pipeline_deps), dependencies=admin_dependencies
        )
    if teams_endpoint is not None:
        fastapi_app.include_router(build_teams_router(teams_endpoint))
    if integration_store is not None and channel_tester is not None:
        fastapi_app.include_router(
            build_integrations_router(integration_store, channel_tester),
            dependencies=admin_dependencies,
        )
    return fastapi_app


def _poller_lifespan(poller: TelegramPoller):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(poller.run(), name="telegram-poller")
        try:
            yield
        finally:
            poller.stop()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return lifespan


def _build_default_deps(
    application: Application | None = None,
    engine: Engine | None = None,
    vector_store: VectorStore | None = None,
) -> IngestDeps:
    """Wire `IngestDeps` from the real composition root: `bootstrap()`'s
    providers plus a real `Engine`/`VectorStore`/`IndexWriter` opened at
    the paths named in `config.yaml`.

    `application`, `engine`, and `vector_store` are accepted (all
    optional) so `__getattr__` below can build each of them exactly once
    and hand the *same* objects to both this function and
    `_build_pipeline_deps` — see that function's docstring for why sharing
    them matters. Called with no arguments (every existing caller,
    including `tests/test_deps_wiring.py`), this builds its own of each,
    unchanged from before.
    """
    application = application or bootstrap()
    cfg = application.config

    if engine is None:
        engine = create_engine_for(Path(cfg.storage.sqlite_path))
        init_schema(engine)
    if vector_store is None:
        vector_store = VectorStore(Path(cfg.storage.faiss_dir), cfg.embedding.dimension)
    index_writer = IndexWriter(
        engine,
        vector_store,
        application.embedding,
        batch_size=cfg.embedding.batch_size,
    )

    return IngestDeps(
        engine=engine,
        cfg=cfg,
        classify_llm=application.classify_llm,
        embedding=application.embedding,
        vector_store=vector_store,
        index_writer=index_writer,
        get_cfg=lambda: application.config,
        classification_preflight=lambda current_cfg: preflight_classifier(
            current_cfg, application.classify_llm
        ),
    )


def _build_pipeline_deps(
    application: Application | None = None,
    engine: Engine | None = None,
    vector_store: VectorStore | None = None,
) -> PipelineDeps:
    """Wire `PipelineDeps` from the real composition root, mirroring
    `_build_default_deps` for the query pipeline's dependencies.

    `application`, `engine`, and `vector_store`, when supplied by
    `__getattr__`, are the *exact same objects* `_build_default_deps` was
    given — sharing one `Engine` and, critically, one `VectorStore`
    between the ingest and query paths. `VectorStore._get_or_create`
    caches a space's index in memory on first touch and never re-reads it
    from disk; two independent `VectorStore` instances over the same
    `faiss_dir` (the previous behaviour: each of these two functions built
    its own) meant a document ingested after the query-side instance had
    already cached a space was invisible to dense retrieval for the rest
    of the process, with no error — `spec: knowledge-retrieval`'s hybrid
    retrieval would silently degrade to keyword-only.

    Called with no arguments (every existing caller other than
    `__getattr__`), this builds its own `Application`/`Engine`/
    `VectorStore`, unchanged from before — `bootstrap()` is ordinary,
    idempotent I/O (read `.env`, read `config.yaml`, construct fresh
    provider objects), so that remains a safe default for a caller that
    only wants the query pipeline wired in isolation.
    """
    application = application or bootstrap()
    cfg = application.config

    if engine is None:
        engine = create_engine_for(Path(cfg.storage.sqlite_path))
        init_schema(engine)
    if vector_store is None:
        vector_store = VectorStore(Path(cfg.storage.faiss_dir), cfg.embedding.dimension)
    centroids = CentroidIndex(application.embedding, cfg)
    reranker = Reranker(cfg.rag.rerank_model)

    return PipelineDeps(
        engine=engine,
        # `application.config` re-reads `application.config_service.
        # current` on every access, so this stays live across any
        # `ConfigService.update()`/`.reload()` made on that same service —
        # see `PipelineDeps.get_cfg`'s docstring (C2).
        get_cfg=lambda: application.config,
        embedding=application.embedding,
        classify_llm=application.classify_llm,
        generate_llm=application.generate_llm,
        vector_store=vector_store,
        centroids=centroids,
        reranker=reranker,
    )


def __getattr__(name: str):
    if name == "app":
        # Built once, here, and threaded into both `_build_default_deps`
        # and `_build_pipeline_deps` so the ingest and query paths share
        # one `Application`, one `Engine`, and — the fix this exists for —
        # one `VectorStore`. See `_build_pipeline_deps`'s docstring.
        application = bootstrap()
        if not application.admin_password:
            raise RuntimeError("ADMIN_PASSWORD must be set before starting the API")
        if not application.credential_encryption_key:
            raise RuntimeError(
                "CREDENTIAL_ENCRYPTION_KEY must be set before starting the API"
            )
        cfg = application.config
        engine = create_engine_for(Path(cfg.storage.sqlite_path))
        init_schema(engine)
        recover_interrupted_documents(engine)
        channel_store = ChannelStore(
            engine,
            application.credential_encryption_key,
            env=application.channel_env,
        )
        channel_store.initialize(
            "telegram", enabled=cfg.channels.telegram.enabled
        )
        channel_store.initialize("teams", enabled=cfg.channels.teams.enabled)
        vector_store = VectorStore(Path(cfg.storage.faiss_dir), cfg.embedding.dimension)

        deps = _build_default_deps(application, engine, vector_store)
        pipeline_deps = _build_pipeline_deps(application, engine, vector_store)
        # I5: load the cross-encoder now, at process startup, rather than
        # deferring it into whichever query happens to be first.
        # `VectorStore` above has already imported faiss; loading the
        # cross-encoder (and therefore torch) here too, in the same
        # process-startup window, fixes their relative import order at a
        # predictable point instead of leaving it to a live request -- see
        # `tests/test_rerank.py::test_3a_6_...`'s docstring for why that
        # ordering matters on this platform (an interpreter abort, not a
        # catchable exception). Also takes model-load latency off the
        # first user's query. `Reranker.score`'s docstring documents
        # exactly this: construct `Reranker` and call `.score()` once
        # during wiring for callers that want it loaded at startup.
        pipeline_deps.reranker.score("warm-up", ["warm-up"])
        query_logger = QueryLogger(engine)
        channel_handler = ChannelHandler(
            channel_store,
            lambda question, profile: answer_question(
                question, profile, pipeline_deps
            ),
            query_logger,
        )
        telegram_poller = TelegramPoller(
            channel_store,
            channel_handler,
            max_message_chars=cfg.channels.telegram.max_message_chars,
        )
        teams_endpoint = TeamsEndpoint(
            channel_store,
            channel_handler,
            max_message_chars=cfg.channels.teams.max_message_chars,
        )
        channel_tester = ChannelTestService(
            channel_store,
            channel_handler,
            telegram_max_chars=cfg.channels.telegram.max_message_chars,
            teams_max_chars=cfg.channels.teams.max_message_chars,
        )
        fastapi_app = create_app(
            deps,
            pipeline_deps,
            admin_password=application.admin_password,
            lifespan=_poller_lifespan(telegram_poller),
            teams_endpoint=teams_endpoint,
            integration_store=channel_store,
            channel_tester=channel_tester,
        )
        fastapi_app.state.channel_store = channel_store
        fastapi_app.state.query_logger = query_logger
        fastapi_app.state.channel_handler = channel_handler
        fastapi_app.state.telegram_poller = telegram_poller
        fastapi_app.state.teams_endpoint = teams_endpoint
        fastapi_app.state.channel_tester = channel_tester
        return fastapi_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
