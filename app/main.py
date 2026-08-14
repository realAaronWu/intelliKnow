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
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI

from sqlalchemy.engine import Engine

from app.analytics.log import QueryLogger
from app.admin.service import AdminService
from app.api.admin import build_admin_router
from app.api.auth import AdminSessionManager, build_admin_auth, build_admin_session_router
from app.api.documents import build_documents_router
from app.api.integrations import build_integrations_router
from app.api.query import build_query_router
from app.bootstrap import Application, bootstrap
from app.channels.handler import ChannelHandler
from app.channels.store import ChannelStore, ensure_no_legacy_secret_references
from app.channels.telegram import TelegramBotAPI, TelegramPoller
from app.channels.teams import TeamsEndpoint, build_teams_router
from app.channels.tester import ChannelTestService
from app.channels.whatsapp import WhatsAppCloudAPI, WhatsAppEndpoint, build_whatsapp_router
from app.db import create_engine_for, init_schema, recover_interrupted_documents
from app.ingest.worker import IngestDeps
from app.ingest.classify_doc import preflight_classifier
from app.orchestrator.centroids import CentroidIndex
from app.orchestrator.pipeline import PipelineDeps, answer_question
from app.orchestrator.errors import ClassificationError
from app.orchestrator.feedback import load_classification_examples
from app.providers.base import ProviderError
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
    whatsapp_endpoint: WhatsAppEndpoint | None = None,
    integration_store: ChannelStore | None = None,
    channel_tester: ChannelTestService | None = None,
    admin_service: AdminService | None = None,
    query_logger: QueryLogger | None = None,
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
    session_manager = AdminSessionManager(admin_password) if admin_password else None
    admin_dependencies = (
        [Depends(build_admin_auth(session_manager))] if session_manager is not None else []
    )
    if session_manager is not None:
        fastapi_app.include_router(build_admin_session_router(session_manager))
    fastapi_app.include_router(
        build_documents_router(deps), dependencies=admin_dependencies
    )
    if pipeline_deps is not None:
        fastapi_app.include_router(
            build_query_router(pipeline_deps, query_logger), dependencies=admin_dependencies
        )
    if teams_endpoint is not None:
        fastapi_app.include_router(build_teams_router(teams_endpoint))
    if whatsapp_endpoint is not None:
        fastapi_app.include_router(build_whatsapp_router(whatsapp_endpoint))
    if integration_store is not None and channel_tester is not None:
        fastapi_app.include_router(
            build_integrations_router(integration_store, channel_tester),
            dependencies=admin_dependencies,
        )
    if admin_service is not None:
        fastapi_app.include_router(
            build_admin_router(admin_service), dependencies=admin_dependencies
        )
    return fastapi_app


def _channel_lifespan(
    poller: TelegramPoller,
    whatsapp_api: WhatsAppCloudAPI | None = None,
    channel_store: ChannelStore | None = None,
):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if whatsapp_api is None:
            task = asyncio.create_task(poller.run(), name="telegram-poller")
            try:
                yield
            finally:
                poller.stop()
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        else:
            async with whatsapp_api:
                if channel_store is not None and channel_store.is_enabled("whatsapp"):
                    try:
                        credentials = channel_store.load_credentials("whatsapp")
                        if credentials is not None:
                            await whatsapp_api.warm_delivery(
                                credentials.values["access_token"],
                                credentials.values["phone_number_id"],
                            )
                    except Exception as exc:
                        # Warm-up is an optimization. The endpoint still gets
                        # its normal delivery attempt and actionable error.
                        logging.getLogger(__name__).warning(
                            "WhatsApp delivery warm-up failed: %s", exc
                        )
                task = asyncio.create_task(poller.run(), name="telegram-poller")
                try:
                    yield
                finally:
                    poller.stop()
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    return lifespan


_poller_lifespan = _channel_lifespan


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
        get_classification_examples=lambda: load_classification_examples(engine),
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
        cfg = application.config
        engine = create_engine_for(Path(cfg.storage.sqlite_path))
        init_schema(engine)
        ensure_no_legacy_secret_references(engine)
        recover_interrupted_documents(engine)
        channel_store = ChannelStore(
            engine,
            application.credential_encryption_key,
        )
        channel_store.initialize(
            "telegram", enabled=cfg.channels.telegram.enabled
        )
        channel_store.initialize("teams", enabled=cfg.channels.teams.enabled)
        channel_store.initialize("whatsapp", enabled=cfg.channels.whatsapp.enabled)
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
        telegram_api_factory = lambda: TelegramBotAPI(
            proxy_url=application.telegram_proxy_url
        )
        telegram_poller = TelegramPoller(
            channel_store,
            channel_handler,
            max_message_chars=cfg.channels.telegram.max_message_chars,
            api_factory=telegram_api_factory,
        )
        teams_endpoint = TeamsEndpoint(
            channel_store,
            channel_handler,
            max_message_chars=cfg.channels.teams.max_message_chars,
        )
        whatsapp_api = WhatsAppCloudAPI(proxy_url=application.whatsapp_proxy_url)
        whatsapp_endpoint = WhatsAppEndpoint(
            channel_store,
            channel_handler,
            max_message_chars=cfg.channels.whatsapp.max_message_chars,
            api_provider=lambda: whatsapp_api,
        )
        channel_tester = ChannelTestService(
            channel_store,
            channel_handler,
            telegram_max_chars=cfg.channels.telegram.max_message_chars,
            teams_max_chars=cfg.channels.teams.max_message_chars,
            whatsapp_max_chars=cfg.channels.whatsapp.max_message_chars,
            telegram_api_factory=telegram_api_factory,
            telegram_api_provider=lambda: telegram_poller.active_api,
            whatsapp_api_provider=lambda: whatsapp_api,
        )

        def validate_intents(proposed_config) -> None:
            # Build proposed centroids without mutating the live index, then
            # verify the classification LLM can return structured output.
            try:
                CentroidIndex(application.embedding, proposed_config)
            except ProviderError as exc:
                raise ClassificationError(
                    f"Classification embeddings are unavailable ({exc.category}); "
                    "nothing was saved. Please retry."
                ) from exc
            preflight_classifier(proposed_config, application.classify_llm)

        admin_service = AdminService(
            engine,
            application.config_service,
            vector_store,
            channel_store,
            intent_validator=validate_intents,
        )
        fastapi_app = create_app(
            deps,
            pipeline_deps,
            admin_password=application.admin_password,
            lifespan=_channel_lifespan(
                telegram_poller, whatsapp_api, channel_store
            ),
            teams_endpoint=teams_endpoint,
            whatsapp_endpoint=whatsapp_endpoint,
            integration_store=channel_store,
            channel_tester=channel_tester,
            admin_service=admin_service,
            query_logger=query_logger,
        )
        fastapi_app.state.channel_store = channel_store
        fastapi_app.state.query_logger = query_logger
        fastapi_app.state.channel_handler = channel_handler
        fastapi_app.state.telegram_poller = telegram_poller
        fastapi_app.state.teams_endpoint = teams_endpoint
        fastapi_app.state.whatsapp_endpoint = whatsapp_endpoint
        fastapi_app.state.channel_tester = channel_tester
        fastapi_app.state.admin_service = admin_service
        return fastapi_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
