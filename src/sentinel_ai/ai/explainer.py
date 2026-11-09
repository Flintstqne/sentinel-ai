"""Optional plain-language explanations of an assessment from a local Ollama model.

The model is a *reader* of findings, never an author of them:

* It receives one finished Assessment and returns a separate AIExplanation. Nothing it says
  is stored on the Assessment or its Evidence, and no result or severity depends on it.
* It is off by default, talks only to a loopback address, and never goes through a proxy.
* Its reply must be JSON in a fixed shape; anything else is discarded, not shown raw.
* Grounding check: a reply that names a file path, IP address, port or quoted username that
  does not appear in the assessment is rejected. The model may rephrase the facts; it may not
  introduce new ones.
* Its text is untrusted: the UI shows it as plain text, labelled as AI-generated.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sentinel_ai.monitoring.model import Assessment

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"  # fits a 6 GB laptop GPU; the 7B model spills into RAM
TIMEOUT_S = 60  # first call includes loading the model from disk
MAX_SUMMARY = 1200
MAX_STEPS = 5
MAX_STEP = 300

SYSTEM_PROMPT = """\
You explain the result of one security check to a non-expert. The JSON you receive is the \
complete and only source of truth.
Rules:
- Use only facts in the given JSON. Do not guess about the system.
- Do not mention any file path, IP address, port number or username that is not in the JSON.
- Do not change or dispute the result or the severity.
- Reply with JSON only: {"summary": "<2-4 plain sentences>", "next_steps": ["<step>", ...]} \
with at most 5 short next steps."""

# Ollama's structured outputs: generation is constrained to this shape, which small models
# otherwise ignore (qwen3:4b echoed its input back). _parse_reply still validates it.
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_STEPS},
    },
    "required": ["summary", "next_steps"],
}

Post = Callable[[str, dict, float], dict]


class ExplanationUnavailable(Exception):
    """Why no explanation can be shown. The message is safe to display."""


@dataclass(frozen=True)
class AIExplanation:
    check_id: str  # which assessment this explains; the assessment itself is never touched
    summary: str
    next_steps: tuple[str, ...]
    model: str
    created_at: str  # ISO 8601 UTC

    @property
    def label(self) -> str:
        return f"AI-generated ({self.model}), may be wrong"


def check_loopback(url: str) -> str:
    """Return url if it is plain http to a loopback host, else raise ExplanationUnavailable."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parts.scheme != "http" or not loopback:
        raise ExplanationUnavailable(f"refusing non-loopback model address {url!r}")
    return url.rstrip("/")


def _http_post(url: str, payload: dict, timeout: float) -> dict:
    # An empty ProxyHandler: an http_proxy in the environment must never see this traffic.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(  # noqa: S310 - scheme and host checked by check_loopback
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read())


def facts(a: Assessment) -> dict:
    """Everything the model may know: the assessment, nothing else."""
    return {
        "control": f"{a.control.control_id}: {a.control.title}",
        "result": a.result.value,
        "severity": a.severity.name.lower(),
        "explanation": a.explanation,
        "evidence": [{"source": e.source, "observation": e.observation} for e in a.evidence],
        "remediation": a.remediation,
    }


_PATH = re.compile(r"(?<![\w.~<])/(?:[\w.-]+/)*[\w.-]+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PORT = re.compile(
    r"\bports?\s+(\d{1,5})\b|\b(?:tcp|udp)/(\d{1,5})\b|(?:\d\.\d+|\*|\]|localhost):(\d{1,5})\b",
    re.IGNORECASE,
)
_USER = re.compile(r"\buser(?:name)?\s+['\"`]([^'\"`\s]+)['\"`]", re.IGNORECASE)


def ungrounded(text: str, source: str) -> list[str]:
    """Concrete identifiers in text that never appear in source. Empty means grounded."""
    missing = [p.rstrip(".") for p in _PATH.findall(text) if p.rstrip(".") not in source]
    missing += [ip for ip in _IPV4.findall(text) if ip not in source]
    for match in _PORT.finditer(text):
        port = next(g for g in match.groups() if g)
        if not re.search(rf"(?<!\d){port}(?!\d)", source):
            missing.append(f"port {port}")
    missing += [f"user {u}" for u in _USER.findall(text) if u not in source]
    return missing


def _parse_reply(content: str) -> tuple[str, tuple[str, ...]]:
    try:
        data = json.loads(content)
    except ValueError:
        raise ExplanationUnavailable("the model did not reply with valid JSON") from None
    summary = data.get("summary") if isinstance(data, dict) else None
    steps = data.get("next_steps", []) if isinstance(data, dict) else None
    if not isinstance(summary, str) or not summary.strip() or not isinstance(steps, list):
        raise ExplanationUnavailable("the model reply was not in the expected shape")
    if not all(isinstance(s, str) for s in steps):
        raise ExplanationUnavailable("the model reply was not in the expected shape")
    too_long = len(summary) > MAX_SUMMARY or any(len(s) > MAX_STEP for s in steps)
    if too_long or len(steps) > MAX_STEPS:
        raise ExplanationUnavailable("the model reply was longer than allowed")
    return summary.strip(), tuple(s.strip() for s in steps if s.strip())


def explain(
    a: Assessment,
    model: str = DEFAULT_MODEL,
    url: str = DEFAULT_URL,
    timeout: float = TIMEOUT_S,
    post: Post = _http_post,
) -> AIExplanation:
    """Ask the local model to explain one assessment. Raises ExplanationUnavailable."""
    base = check_loopback(url)
    given = json.dumps(facts(a), ensure_ascii=False)
    payload = {
        "model": model,
        "stream": False,
        "format": REPLY_SCHEMA,
        "think": False,  # thinking models otherwise reason for minutes before answering
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Explain this check result:\n{given}"},
        ],
    }
    try:
        reply = post(f"{base}/api/chat", payload, timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # URLError covers refused connections; TimeoutError is an OSError; ValueError is a
        # body that was not JSON.
        reason = getattr(exc, "reason", exc)
        raise ExplanationUnavailable(f"the local model is not reachable ({reason})") from None
    message = reply.get("message") if isinstance(reply, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise ExplanationUnavailable("the model returned no answer")
    summary, steps = _parse_reply(content)
    missing = ungrounded(" ".join((summary, *steps)), given)
    if missing:
        raise ExplanationUnavailable(
            "discarded: the model mentioned details that are not in the evidence "
            f"({', '.join(missing[:3])})"
        )
    return AIExplanation(
        a.check_id,
        summary,
        steps,
        model,
        datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
