"""Shared inbound workflow used by every platform adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, replace
from typing import Callable, Literal, Protocol

from app.channels.base import ChannelAdapter, InboundMessage
from app.channels.store import ChannelStore
from app.orchestrator.pipeline import QueryOutcome
from app.rag.format import format_for_channel
from app.rag.generate import ChannelProfile

logger = logging.getLogger(__name__)

_TEXT_ONLY_MESSAGE = "Please send a text question."
_FAILURE_MESSAGE = "Sorry, I couldn't answer that question. Please try again."
_TYPING_TIMEOUT_SECONDS = 0.4


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
    failure_stage: Literal["pipeline", "delivery"] | None = None


Pipeline = Callable[[str, ChannelProfile], QueryOutcome]


class ChannelHandler:
    def __init__(
        self,
        store: ChannelStore,
        pipeline: Pipeline,
        query_logger: QueryLogSink,
        *,
        timer: Callable[[], float] = time.perf_counter,
        typing_timeout_seconds: float = _TYPING_TIMEOUT_SECONDS,
    ) -> None:
        self._store = store
        self._pipeline = pipeline
        self._query_logger = query_logger
        self._timer = timer
        self._typing_timeout_seconds = typing_timeout_seconds

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

        typing_ms = 0
        pipeline_wait_ms = 0

        async def send_typing() -> Exception | None:
            nonlocal typing_ms
            stage_start = self._timer()
            try:
                await adapter.typing(message.reply_ref)
            except Exception as exc:
                return exc
            finally:
                typing_ms = self._elapsed(stage_start)
            return None

        async def run_pipeline() -> QueryOutcome:
            nonlocal pipeline_wait_ms
            stage_start = self._timer()
            try:
                return await asyncio.to_thread(
                    self._pipeline, text, adapter.profile
                )
            finally:
                pipeline_wait_ms = self._elapsed(stage_start)

        # Complete the acknowledgement before using the same platform client
        # for delivery. The strict timeout prevents a slow typing endpoint from
        # consuming the answer budget or competing with the actual send.
        try:
            typing_result = await asyncio.wait_for(
                send_typing(), timeout=self._typing_timeout_seconds
            )
        except TimeoutError:
            typing_result = None
        try:
            pipeline_result: QueryOutcome | Exception = await run_pipeline()
        except Exception as exc:
            pipeline_result = exc

        if isinstance(typing_result, Exception):
            logger.warning(
                "%s typing indicator failed: %s", message.channel, typing_result
            )
            self._store.record_error(message.channel, str(typing_result))
        if isinstance(pipeline_result, Exception):
            return await self._pipeline_failure(
                message, adapter, start, pipeline_result
            )
        outcome = pipeline_result

        try:
            delivery_start = self._timer()
            await adapter.send(message.reply_ref, outcome.answer)
        except Exception as exc:
            latency = self._elapsed(start)
            self._store.mark_disconnected(message.channel, str(exc))
            self._safe_log_failure(message, str(exc), latency, outcome)
            return HandlerResult(
                True, False, "failed", latency, str(exc), "delivery"
            )

        latency = self._elapsed(start)
        channel_timings = {
            **(outcome.timings_ms or {}),
            "channel_typing": typing_ms,
            "channel_pipeline_wait": pipeline_wait_ms,
            "channel_delivery": self._elapsed(delivery_start),
            "end_to_end": latency,
        }
        outcome = replace(outcome, timings_ms=channel_timings)
        logger.info(
            "%s query latency %s",
            message.channel,
            json.dumps(channel_timings, sort_keys=True),
        )
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
            return HandlerResult(
                True, False, "failed", latency, str(send_error), "delivery"
            )

        latency = self._elapsed(start)
        self._store.mark_connected(message.channel, message.reply_ref)
        self._safe_log_failure(message, str(error), latency)
        return HandlerResult(
            True, True, "failed", latency, str(error), "pipeline"
        )

    def _delivery_failure(
        self, message: InboundMessage, start: float, error: Exception
    ) -> HandlerResult:
        latency = self._elapsed(start)
        self._store.mark_disconnected(message.channel, str(error))
        return HandlerResult(True, False, "failed", latency, str(error), "delivery")

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
