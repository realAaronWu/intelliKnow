"""Platform-neutral channel contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.rag.generate import ChannelProfile


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    user_ref: str | None
    text: str | None
    reply_ref: str


class ChannelAdapter(Protocol):
    channel: str
    profile: ChannelProfile

    async def typing(self, reply_ref: str) -> None: ...

    async def send(self, reply_ref: str, text: str) -> None: ...
