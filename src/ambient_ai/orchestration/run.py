"""run_mention: the orchestration path the gateway calls from handle_mention.

plan -> recall (Cortex) -> execute (GitHub, sender's token) -> synthesize -> reply text.
Every step emits one telemetry event. A failure in recall or execute becomes the honest
short reply from ADR-009; the exception class goes to telemetry, never to the SMS.
Every reply, fallback or synthesized, leaves on one orchestration.reply event.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from pydantic_ai import Agent
from pydantic_ai.models import Model

from ambient_ai.identity.senders import SenderProfile
from ambient_ai.orchestration import context_agent
from ambient_ai.orchestration.execution_agent import build_execution_agent
from ambient_ai.orchestration.llm import SYNTHESIS_SETTINGS, llm_model
from ambient_ai.orchestration.orchestrator import Plan, build_orchestrator
from ambient_ai.settings import SMS_REPLY_MAX_CHARS
from ambient_ai.telemetry import log, redact
from ambient_ai.tools.credentials import scrub
from ambient_ai.tools.github_rest import GitHubDeps, github_client

RecallFn = Callable[[SenderProfile], Awaitable[list[str]]]
CredentialsFn = Callable[[str, str], Awaitable[str]]

GITHUB_UNAVAILABLE_REPLY = "I couldn't reach GitHub just now. Try again in a minute."
MEMORY_UNAVAILABLE_REPLY = "I couldn't reach my memory just now. Try again in a minute."
LLM_UNAVAILABLE_REPLY = "I couldn't think that through just now. Try again in a minute."
GITHUB_NOT_CONNECTED_REPLY = (
    "I need your GitHub connected before I can check that. Paste a token in your portal link."
)
CREDENTIALS_PRIVATE_ONLY_REPLY = "Manage your tools in a private chat with me."
"""A group is the wrong room for one person's credential, so the intent is refused there and
nothing is read from or written to the store."""

SYNTHESIS_INSTRUCTIONS = """\
You are Ambient AI, a phone contact replying in an SMS group chat.
Write the reply to the sender's message from the facts and findings you are given.
Plain text only: no markdown, no bullet points, no headings, no emoji.
At most 480 characters and at most four short sentences. Name the repository and the
short commit sha when they are known. If a finding says something could not be found,
say so plainly. Never invent details that are not in the findings.
"""


def build_synthesizer(model: Model | str | None = None) -> Agent[None, str]:
    return Agent(
        llm_model(model),
        output_type=str,
        instructions=SYNTHESIS_INSTRUCTIONS,
        name="synthesis",
        model_settings=SYNTHESIS_SETTINGS,
    )


def clip_sms(text: str) -> str:
    text = " ".join(text.split())
    for ch in ("**", "`", "#"):
        text = text.replace(ch, "")
    if len(text) <= SMS_REPLY_MAX_CHARS:
        return text
    cut = text[: SMS_REPLY_MAX_CHARS - 1]
    if " " in cut[-40:]:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


FALLBACK_REPLIES = frozenset(
    {
        GITHUB_UNAVAILABLE_REPLY,
        MEMORY_UNAVAILABLE_REPLY,
        LLM_UNAVAILABLE_REPLY,
        GITHUB_NOT_CONNECTED_REPLY,
        CREDENTIALS_PRIVATE_ONLY_REPLY,
    }
)
"""Every reply this module returns without a model writing it."""


def is_fallback_reply(text: str) -> bool:
    """True when the reply is one of this module's constants, not an answer to the sender.

    A fallback says only that something was unreachable, so a transcript ending in one holds
    no fact worth remembering; extracting from it invents one.
    """
    return text in FALLBACK_REPLIES


def fallback(who: str, reply: str, **fields: object) -> str:
    """Emit the reply event for an early return, then hand the text back to the caller.

    Every early return goes out through here, on the same `orchestration.reply` event the
    synthesis path emits, so the projector shows which honest sentence went to the sender
    next to the step that failed. Without it the stream shows a failed step and no reply.
    """
    log.emit("orchestration.reply", sender=who, chars=len(reply), path="fallback", **fields)
    return reply


async def run_mention(
    sender: SenderProfile,
    body: str,
    *,
    model: Model | str | None = None,
    recall_fn: RecallFn | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    private: bool = True,
    credentials_fn: CredentialsFn | None = None,
) -> str:
    """Answer one @mention for one sender. Returns the SMS text; the gateway sends it.

    `private` says the conversation is one-to-one (SMS always is; a Telegram private chat is);
    `credentials_fn` is how the gateway applies a credential action it alone can perform.
    Keyword arguments are test seams: `model` replaces the LLM for every agent, `recall_fn`
    replaces the Cortex read, `transport` replaces the network under the GitHub client.
    """
    who = redact(sender.phone)
    recall = recall_fn or context_agent.recall
    body = scrub(body)

    try:
        result = await build_orchestrator(model).run(
            f"GitHub connected: {'yes' if sender.github_token else 'no'}.\n"
            f"Message: {body}"
        )
        plan: Plan = result.output
    except Exception as exc:
        log.emit("orchestration.plan", sender=who, status="failed", error=type(exc).__name__)
        return fallback(who, LLM_UNAVAILABLE_REPLY)
    log.emit(
        "orchestration.plan",
        sender=who,
        status="ok",
        needs_memory=plan.needs_memory,
        needs_github=plan.needs_github,
        direct=plan.direct_reply is not None,
    )
    if plan.credentials_action != "none":
        if not private or credentials_fn is None:
            return fallback(who, CREDENTIALS_PRIVATE_ONLY_REPLY, status="refused")
        tool = plan.credentials_tool or "github"
        reply = clip_sms(await credentials_fn(plan.credentials_action, tool))
        log.emit("orchestration.reply", sender=who, chars=len(reply), path="credentials")
        return reply

    if plan.direct_reply and not (plan.needs_memory or plan.needs_github):
        reply = clip_sms(plan.direct_reply)
        log.emit("orchestration.reply", sender=who, chars=len(reply), path="direct")
        return reply

    facts: list[str] = []
    if plan.needs_memory:
        try:
            facts = await recall(sender)
        except Exception as exc:
            log.emit(
                "orchestration.recall", sender=who, status="failed", error=type(exc).__name__
            )
            return fallback(who, MEMORY_UNAVAILABLE_REPLY)
        log.emit("orchestration.recall", sender=who, status="ok", facts=len(facts))

    findings: str | None = None
    if plan.needs_github:
        if not sender.github_token:
            log.emit("orchestration.execute", sender=who, status="skipped", reason="no token")
            return fallback(who, GITHUB_NOT_CONNECTED_REPLY)
        prompt = f"Task: {plan.github_task or body}\n"
        if facts:
            prompt += "Known facts about this person:\n" + "\n".join(f"- {f}" for f in facts)
        try:
            async with github_client(sender.github_token, transport) as client:
                execution = build_execution_agent(model)
                findings = (await execution.run(prompt, deps=GitHubDeps(client=client))).output
        except Exception as exc:
            log.emit(
                "orchestration.execute", sender=who, status="failed", error=type(exc).__name__
            )
            return fallback(who, GITHUB_UNAVAILABLE_REPLY)
        log.emit("orchestration.execute", sender=who, status="ok", chars=len(findings))

    synthesis_prompt = f"Sender's message: {body}\n"
    synthesis_prompt += "Facts from memory:\n" + (
        "\n".join(f"- {f}" for f in facts) if facts else "- none\n"
    )
    synthesis_prompt += f"\nFindings from GitHub:\n{findings or 'none'}"
    try:
        reply = clip_sms((await build_synthesizer(model).run(synthesis_prompt)).output)
    except Exception as exc:
        log.emit(
            "orchestration.synthesize", sender=who, status="failed", error=type(exc).__name__
        )
        return fallback(who, LLM_UNAVAILABLE_REPLY)
    log.emit("orchestration.synthesize", sender=who, status="ok", chars=len(reply))
    log.emit("orchestration.reply", sender=who, chars=len(reply), path="synthesis")
    return reply
