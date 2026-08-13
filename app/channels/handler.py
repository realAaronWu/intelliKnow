"""Shared inbound workflow used by every platform adapter."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from app.channels.base import ChannelAdapter, InboundMessage
from app.channels.store import ChannelStore
from app.orchestrator.pipeline import QueryOutcome
from app.rag.format import format_for_channel
from app.rag.generate import ChannelProfile

logger = logging.getLogger(__name__)

_TEXT_ONLY_MESSAGE = "Please send a text question."
_FAILURE_MESSAGE = "Sorry, I couldn't answer that question. Please try again."


class QueryLogSink(Protocol):
    def record(
        self, message: InboundMessage, outcome: QueryOutcome, latency_ms: int
    ) -> None: ...

    def record_failure(
        self,
        message: InboundMessage,
        error: str,
        latency_ms: int,
        outcome: QueryOutcome | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class HandlerResult:
    accepted: bool
    delivered: bool
    status: Literal["ignored", "unsupported", "success", "no_match", "failed"]
    latency_ms: int
    error: str | None = None


Pipeline = Callable[[str, ChannelProfile], QueryOutcome]


class ChannelHandler:
    def __init__(
        self,
        store: ChannelStore,
        pipeline: Pipeline,
        query_logger: QueryLogSink,
        *,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._store = store
        self._pipeline = pipeline
        self._query_logger = query_logger
        self._timer = timer

    async def handle(
        self, message: InboundMessage, adapter: ChannelAdapter
    ) -> HandlerResult:
        if message.channel != adapter.channel:
            raise ValueError(
                f"message channel {message.channel!r} does not match adapter "
                f"{adapter.channel!r}"
            )
        if not self._store.is_enabled(message.channel):
            return HandlerResult(False, False, "ignored", 0)

        start = self._timer()
        text = (message.text or "").strip()
        if not text:
            formatted = format_for_channel(_TEXT_ONLY_MESSAGE, [], adapter.profile)
            try:
                await adapter.send(message.reply_ref, formatted)
            except Exception as exc:
                return self._delivery_failure(message, start, exc)
            self._store.mark_connected(message.channel, message.reply_ref)
            return HandlerResult(True, True, "unsupported", self._elapsed(start))

        try:
            await adapter.typing(message.reply_ref)
        except Exception as exc:
            logger.warning("%s typing indicator failed: %s", message.channel, exc)
            self._store.record_error(message.channel, str(exc))

        try:
            outcome = self._pipeline(text, adapter.profile)
        except Exception as exc:
            return await self._pipeline_failure(message, adapter, start, exc)

        try:
            await adapter.send(message.reply_ref, outcome.answer)
        except Exception as exc:
            latency = self._elapsed(start)
            self._store.mark_disconnected(message.channel, str(exc))
            self._safe_log_failure(message, str(exc), latency, outcome)
            return HandlerResult(True, False, "failed", latency, str(exc))

        latency = self._elapsed(start)
        self._store.mark_connected(message.channel, message.reply_ref)
        self._safe_log(message, outcome, latency)
        return HandlerResult(True, True, outcome.status, latency, outcome.error)

    async def _pipeline_failure(
        self,
        message: InboundMessage,
        adapter: ChannelAdapter,
        start: float,
        error: Exception,
    ) -> HandlerResult:
        formatted = format_for_channel(_FAILURE_MESSAGE, [], adapter.profile)
        try:
            await adapter.send(message.reply_ref, formatted)
        except Exception as send_error:
            latency = self._elapsed(start)
            self._store.mark_disconnected(message.channel, str(send_error))
            self._safe_log_failure(message, str(send_error), latency)
            return HandlerResult(True, False, "failed", latency, str(send_error))

        latency = self._elapsed(start)
        self._store.mark_connected(message.channel, message.reply_ref)
        self._safe_log_failure(message, str(error), latency)
        return HandlerResult(True, True, "failed", latency, str(error))

    def _delivery_failure(
        self, message: InboundMessage, start: float, error: Exception
    ) -> HandlerResult:
        latency = self._elapsed(start)
        self._store.mark_disconnected(message.channel, str(error))
        return HandlerResult(True, False, "failed", latency, str(error))

    def _safe_log(
        self, message: InboundMessage, outcome: QueryOutcome, latency_ms: int
    ) -> None:
        try:
            self._query_logger.record(message, outcome, latency_ms)
        except Exception:
            logger.exception("query logging failed after delivery")

    def _safe_log_failure(
        self,
        message: InboundMessage,
        error: str,
        latency_ms: int,
        outcome: QueryOutcome | None = None,
    ) -> None:
        try:
            self._query_logger.record_failure(message, error, latency_ms, outcome)
        except Exception:
            logger.exception("failure logging failed after delivery")

    def _elapsed(self, start: float) -> int:
        return max(0, round((self._timer() - start) * 1000))
