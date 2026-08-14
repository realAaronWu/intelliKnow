"""Answer generation — one grounded LLM call over an assembled context.

`generate_answer` builds a single prompt from three ingredients — the
grounding/citation rules, the destination channel's fit constraints, and
the context bundle from `app/rag/context.py` — and returns whatever the
model wrote back, unmodified. It does not verify citations (that is
`app/rag/citations.py`, deliberately a separate, model-free pass) and it
does not enforce the channel's character limit (that is
`app/rag/format.py`'s job, and it is a hard guarantee rather than this
module's best-effort prompt instruction).

`ChannelProfile` carries only what the prompt needs to know about the
destination: how much room there is and what markup it can render. It
says nothing about transport (webhook vs. polling, credentials, etc.) —
that lives in `app.config.ChannelConfig` — because a prompt has no
business knowing how the message gets delivered, only what it will look
like once it arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.providers.base import LLMProvider
from app.rag.context import ContextBundle

Markup = Literal["markdownv2", "html", "plain"]

# The configured Claude model may use part of this budget for internal
# reasoning before producing the visible answer. Keep enough headroom for
# reasoning while the prompt's 80-word limit keeps the delivered answer short.
_ANSWER_MAX_TOKENS = 1024


@dataclass(frozen=True)
class ChannelProfile:
    name: str
    max_chars: int
    markup: Markup
    supports_lists: bool


_SYSTEM_PROMPT_TEMPLATE = """\
You are a knowledge assistant. Answer the user's question using only the \
information given in the context below the question — never rely on \
outside knowledge, even if you happen to know the answer.

Rules:
- Answer only from the supplied context. Do not use information that is \
not present there.
- If the context does not contain the answer, say so plainly (e.g. "I \
don't have that information") instead of guessing.
- Cite every factual claim using the bracketed marker(s) exactly as they \
appear in the context, e.g. [1] or [1][2]. Never cite a marker that is \
not present in the context.
- Answer directly in at most 80 words unless the user explicitly asks for \
more detail. Include only information needed to answer the question.

Delivery channel: {channel_name}. Your reply is limited to {max_chars} \
characters and will be rendered as {markup}{lists_note}. Write to fit \
within that limit.\
"""


def _lists_note(channel: ChannelProfile) -> str:
    if channel.supports_lists:
        return ", which supports bulleted/numbered lists"
    return ", which does not render lists — write plain sentences instead"


def _build_system_prompt(channel: ChannelProfile) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        channel_name=channel.name,
        max_chars=channel.max_chars,
        markup=channel.markup,
        lists_note=_lists_note(channel),
    )


def _build_user_prompt(question: str, bundle: ContextBundle) -> str:
    if bundle.sources:
        context_section = bundle.prompt_block
    else:
        context_section = "(no relevant context was found)"
    return f"Question: {question}\n\nContext:\n{context_section}"


def generate_answer(
    question: str, bundle: ContextBundle, channel: ChannelProfile, llm: LLMProvider
) -> str:
    """Generate an answer to `question` grounded in `bundle`, written to
    fit `channel`.

    Makes exactly one `llm.complete()` call. A `ProviderError` raised by
    the provider is not caught here — it propagates so the caller (the
    pipeline / channel adapter) can convert it into a user-facing message.
    """
    system = _build_system_prompt(channel)
    user = _build_user_prompt(question, bundle)
    result = llm.complete(
        system=system,
        user=user,
        max_tokens=_ANSWER_MAX_TOKENS,
    )
    return result.text
