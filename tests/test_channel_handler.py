from __future__ import annotations

import asyncio

from cryptography.fernet import Fernet

from app.channels.base import InboundMessage
from app.channels.handler import ChannelHandler
from app.channels.store import ChannelStore
from app.db import create_engine_for, init_schema
from app.orchestrator.pipeline import QueryOutcome
from app.rag.generate import ChannelProfile


class FakeAdapter:
    channel = "telegram"
    profile = ChannelProfile("telegram", 4096, "markdownv2", False)

    def __init__(self, events: list[str]):
        self.events = events
        self.sent: list[tuple[str, str]] = []
        self.typing_error: Exception | None = None
        self.send_error: Exception | None = None
        self.typing_gate: asyncio.Event | None = None
        self.typing_cancelled = False

    async def typing(self, reply_ref: str) -> None:
        self.events.append("typing")
        if self.typing_error:
            raise self.typing_error
        if self.typing_gate is not None:
            try:
                await self.typing_gate.wait()
            except asyncio.CancelledError:
                self.typing_cancelled = True
                raise

    async def send(self, reply_ref: str, text: str) -> None:
        self.events.append("send")
        if self.send_error:
            raise self.send_error
        self.sent.append((reply_ref, text))


class FakeLogger:
    def __init__(self, events: list[str]):
        self.events = events
        self.records = []
        self.fail = False

    def record(self, message, outcome, latency_ms):
        self.events.append("log")
        if self.fail:
            raise RuntimeError("logging failed")
        self.records.append((message, outcome, latency_ms))

    def record_failure(self, message, error, latency_ms, outcome=None):
        self.events.append("log_failure")
        if self.fail:
            raise RuntimeError("logging failed")
        self.records.append((message, outcome, latency_ms, error))


def _outcome(status="success"):
    return QueryOutcome(
        answer="already\\-formatted answer",
        citations=[],
        intent_slug="hr",
        confidence=0.9,
        classified_by="centroid",
        reasoning=None,
        classification_failed=False,
        fallback_used=False,
        status=status,
        retrieved_doc_ids=[1],
        latency_ms=100,
        error="provider failed" if status == "failed" else None,
    )


def _store(tmp_path):
    engine = create_engine_for(tmp_path / "handler.db")
    init_schema(engine)
    store = ChannelStore(
        engine,
        Fernet.generate_key().decode("ascii"),
    )
    store.set_enabled("telegram", True)
    return store


def test_handler_orders_typing_pipeline_send_then_log_and_does_not_reformat(tmp_path):
    events: list[str] = []
    adapter = FakeAdapter(events)
    logger = FakeLogger(events)

    def pipeline(question, profile):
        events.append("pipeline")
        return _outcome()

    handler = ChannelHandler(_store(tmp_path), pipeline, logger)
    message = InboundMessage("telegram", "user", "Question", "chat-1")

    result = asyncio.run(handler.handle(message, adapter))

    assert events == ["typing", "pipeline", "send", "log"]
    assert adapter.sent == [("chat-1", "already\\-formatted answer")]
    assert result.delivered is True
    assert result.status == "success"
    assert logger.records[0][2] >= 0
    timings = logger.records[0][1].timings_ms
    assert timings is not None
    assert set(("channel_typing", "channel_pipeline_wait", "channel_delivery", "end_to_end")) <= set(timings)


def test_typing_failure_is_retained_but_does_not_block_delivery(tmp_path):
    events: list[str] = []
    adapter = FakeAdapter(events)
    adapter.typing_error = RuntimeError("typing unavailable")
    store = _store(tmp_path)
    handler = ChannelHandler(store, lambda question, profile: _outcome(), FakeLogger(events))

    result = asyncio.run(
        handler.handle(InboundMessage("telegram", "user", "Question", "chat-1"), adapter)
    )

    assert result.delivered is True
    assert store.get("telegram").status == "connected"
    assert "typing unavailable" in store.get("telegram").last_error


def test_slow_typing_request_is_cancelled_before_answer_delivery(tmp_path):
    events: list[str] = []
    adapter = FakeAdapter(events)
    adapter.typing_gate = asyncio.Event()
    handler = ChannelHandler(
        _store(tmp_path),
        lambda question, profile: _outcome(),
        FakeLogger(events),
        typing_timeout_seconds=0.01,
    )

    result = asyncio.run(
        handler.handle(InboundMessage("telegram", "user", "Question", "chat-1"), adapter)
    )

    assert result.delivered is True
    assert adapter.typing_cancelled is True
    assert events[-2:] == ["send", "log"]


def test_non_text_message_gets_a_reply_without_pipeline_or_log(tmp_path):
    events: list[str] = []
    adapter = FakeAdapter(events)
    logger = FakeLogger(events)

    def should_not_run(question, profile):
        raise AssertionError("pipeline should not run")

    handler = ChannelHandler(_store(tmp_path), should_not_run, logger)
    message = InboundMessage("telegram", "user", None, "chat-1")

    result = asyncio.run(handler.handle(message, adapter))

    assert result.status == "unsupported"
    assert result.delivered is True
    assert "text" in adapter.sent[0][1].lower()
    assert logger.records == []


def test_disabled_channel_ignores_message(tmp_path):
    store = _store(tmp_path)
    store.set_enabled("telegram", False)
    events: list[str] = []
    handler = ChannelHandler(store, lambda question, profile: _outcome(), FakeLogger(events))

    result = asyncio.run(
        handler.handle(
            InboundMessage("telegram", "user", "Question", "chat-1"),
            FakeAdapter(events),
        )
    )

    assert result.accepted is False
    assert events == []


def test_pipeline_exception_sends_user_error_and_records_failure(tmp_path):
    events: list[str] = []
    adapter = FakeAdapter(events)
    logger = FakeLogger(events)

    def broken(question, profile):
        events.append("pipeline")
        raise RuntimeError("pipeline exploded")

    handler = ChannelHandler(_store(tmp_path), broken, logger)
    result = asyncio.run(
        handler.handle(InboundMessage("telegram", "user", "Question", "chat-1"), adapter)
    )

    assert result.status == "failed"
    assert result.delivered is True
    assert events == ["typing", "pipeline", "send", "log_failure"]
    assert "try again" in adapter.sent[0][1].lower()


def test_send_failure_disconnects_channel_and_is_logged(tmp_path):
    events: list[str] = []
    adapter = FakeAdapter(events)
    adapter.send_error = RuntimeError("platform send failed")
    logger = FakeLogger(events)
    store = _store(tmp_path)
    handler = ChannelHandler(store, lambda question, profile: _outcome(), logger)

    result = asyncio.run(
        handler.handle(InboundMessage("telegram", "user", "Question", "chat-1"), adapter)
    )

    assert result.delivered is False
    assert result.status == "failed"
    assert store.get("telegram").status == "disconnected"
    assert events[-1] == "log_failure"


def test_logging_failure_never_changes_successful_delivery(tmp_path):
    events: list[str] = []
    logger = FakeLogger(events)
    logger.fail = True
    adapter = FakeAdapter(events)
    handler = ChannelHandler(_store(tmp_path), lambda question, profile: _outcome(), logger)

    result = asyncio.run(
        handler.handle(InboundMessage("telegram", "user", "Question", "chat-1"), adapter)
    )

    assert result.delivered is True
    assert adapter.sent
