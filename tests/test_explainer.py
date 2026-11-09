import copy
import json
import socket
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sentinel_ai.ai import explainer
from sentinel_ai.ai.explainer import ExplanationUnavailable, explain, ungrounded
from sentinel_ai.monitoring.model import Assessment, Control, Evidence, Result, Severity

A = Assessment(
    "listening_ports",
    Control("NET-01", "Only intended network services are exposed", "ref"),
    Result.WARNING,
    Severity.MEDIUM,
    "One TCP service listens on all interfaces.",
    (
        Evidence("ss -tulnHp", "tcp 0.0.0.0:631 cupsd"),
        Evidence("/etc/cups/cupsd.conf", "Listen *:631"),
    ),
    "Bind cupsd to 127.0.0.1 in `/etc/cups/cupsd.conf`.",
)


def reply(summary="The printing service is reachable from the network on port 631.", steps=None):
    content = json.dumps({"summary": summary, "next_steps": steps or ["Check it is intended."]})
    return lambda url, payload, timeout: {"message": {"role": "assistant", "content": content}}


def test_valid_grounded_reply_becomes_a_separate_labelled_explanation() -> None:
    before = copy.deepcopy(A)
    out = explain(A, post=reply())
    assert out.check_id == "listening_ports" and out.next_steps == ("Check it is intended.",)
    assert "AI-generated" in out.label and explainer.DEFAULT_MODEL in out.label
    assert before == A  # untouched


def test_request_carries_only_the_assessment_and_asks_for_json() -> None:
    seen = {}

    def post(url, payload, timeout):
        seen.update(url=url, payload=payload)
        return reply()(url, payload, timeout)

    explain(A, post=post)
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert (
        seen["payload"]["format"] == explainer.REPLY_SCHEMA and seen["payload"]["stream"] is False
    )
    sent = seen["payload"]["messages"][1]["content"]
    assert json.loads(sent.split("\n", 1)[1]) == explainer.facts(A)


@pytest.mark.parametrize(
    "text",
    [
        "Port 22 is open.",  # hallucinated port
        "Traffic from 10.0.0.5 was seen.",  # invented IP
        "Edit /etc/ssh/sshd_config.",  # file not in the evidence
        "The user 'admin' logged in.",  # invented user
        "It listens on *:8080.",
    ],
)
def test_hallucinated_details_are_rejected(text) -> None:
    with pytest.raises(ExplanationUnavailable, match="not in the evidence"):
        explain(A, post=reply(summary=text))


def test_details_that_are_in_the_evidence_are_allowed() -> None:
    assert not ungrounded(
        "Port 631 on 0.0.0.0 via /etc/cups/cupsd.conf.", json.dumps(explainer.facts(A))
    )
    explain(A, post=reply(steps=["Edit /etc/cups/cupsd.conf to listen on 127.0.0.1:631."]))


def test_a_hallucination_hidden_in_next_steps_is_also_rejected() -> None:
    with pytest.raises(ExplanationUnavailable):
        explain(A, post=reply(steps=["Run sudo ufw deny 3306", "Close port 3306"]))


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"summary": 5}',
        '{"summary": "x", "next_steps": "y"}',
        '{"summary": "", "next_steps": []}',
        '{"summary": "x", "next_steps": [1]}',
        json.dumps({"summary": "x" * 5000, "next_steps": []}),
        json.dumps({"summary": "x", "next_steps": ["a"] * 9}),
    ],
)
def test_malformed_or_oversized_replies_show_nothing(content) -> None:
    with pytest.raises(ExplanationUnavailable):
        explain(A, post=lambda u, p, t: {"message": {"content": content}})


@pytest.mark.parametrize("raw", [{}, {"message": "x"}, [], {"message": {"content": None}}])
def test_odd_server_bodies_show_nothing(raw) -> None:
    with pytest.raises(ExplanationUnavailable, match="no answer"):
        explain(A, post=lambda u, p, t: raw)


def test_markup_in_the_reply_is_kept_as_inert_text() -> None:
    out = explain(A, post=reply(summary="<b>bold</b><script>x()</script>"))
    assert out.summary == "<b>bold</b><script>x()</script>"  # the UI shows it as plain text


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:11434",
        "https://127.0.0.1:11434",
        "http://example.com",
        "http://127.0.0.1.evil.com",
        "ftp://localhost",
        "http://[::2]:11434",
    ],
)
def test_non_loopback_addresses_are_refused_before_any_request(url) -> None:
    calls = []
    with pytest.raises(ExplanationUnavailable, match="non-loopback"):
        explain(A, url=url, post=lambda *args: calls.append(args))
    assert calls == []


@pytest.mark.parametrize(
    "url", ["http://localhost:11434", "http://127.0.0.2:1", "http://[::1]:11434/"]
)
def test_loopback_addresses_are_accepted(url) -> None:
    assert explainer.check_loopback(url)


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError(ConnectionRefusedError(111, "refused")),
        TimeoutError("timed out"),
        ValueError("bad body"),
    ],
)
def test_transport_failures_are_unavailable_not_crashes(exc) -> None:
    def post(*_):
        raise exc

    with pytest.raises(ExplanationUnavailable, match="not reachable"):
        explain(A, post=post)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_http_refused_connection(monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")  # must be ignored
    with pytest.raises(ExplanationUnavailable, match="not reachable"):
        explain(A, url=f"http://127.0.0.1:{free_port()}", timeout=2)


def test_real_http_round_trip_and_timeout(monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")  # a proxy would break this request
    delay = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if delay.is_set():
                delay.wait(0)  # fall through to a slow reply
                import time

                time.sleep(1.5)
            body = json.dumps(reply()(None, None, None)).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert "631" in explain(A, url=url, timeout=5).summary
        delay.set()
        with pytest.raises(ExplanationUnavailable, match="not reachable"):
            explain(A, url=url, timeout=0.3)
    finally:
        server.shutdown()
        server.server_close()
