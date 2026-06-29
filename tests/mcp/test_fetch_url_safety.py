"""Safety contract for external URL reads."""
from __future__ import annotations

import contextlib

import pytest


class _FakeHeaders:
    def get(self, name: str, default: str = "") -> str:
        if name.lower() == "content-type":
            return "text/plain; charset=utf-8"
        return default


class _FakeResponse:
    status = 200
    headers = _FakeHeaders()

    def __init__(self, final_url: str = "https://example.com/doc.txt") -> None:
        self._final_url = final_url

    def read(self, _limit: int) -> bytes:
        return b"hello"

    def geturl(self) -> str:
        return self._final_url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_url_requires_user_provided_or_external_confirmation_before_network(ctx, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("network must not be touched before external-read scope is declared")

    monkeypatch.setattr(ctx.urllib.request, "urlopen", fail_if_called)

    with pytest.raises(ctx.McpError, match="user_provided=true"):
        ctx.tool_fetch_url({"url": "https://example.com/doc.txt"})


def test_fetch_url_blocks_private_and_local_hosts(ctx, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("private/internal URLs must be rejected before network")

    monkeypatch.setattr(ctx.urllib.request, "urlopen", fail_if_called)

    blocked = [
        "http://localhost:5000/",
        "http://127.0.0.1:5000/",
        "http://10.0.0.5/secret",
        "http://172.16.0.2/secret",
        "http://192.168.1.10/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://service.local/status",
    ]
    for url in blocked:
        with pytest.raises(ctx.McpError, match="private|localhost|internal|reserved"):
            ctx.tool_fetch_url({"url": url, "user_provided": True})


def test_fetch_url_blocks_redirect_to_private_host(ctx, monkeypatch):
    @contextlib.contextmanager
    def fake_urlopen(_request, timeout: int = 30):
        yield _FakeResponse("http://127.0.0.1:5000/after-redirect")

    monkeypatch.setattr(ctx.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ctx.McpError, match="private|localhost|internal|reserved"):
        ctx.tool_fetch_url({"url": "https://example.com/redirect", "user_provided": True})


def test_fetch_url_allows_user_provided_public_url(ctx, monkeypatch):
    @contextlib.contextmanager
    def fake_urlopen(_request, timeout: int = 30):
        yield _FakeResponse("https://example.com/doc.txt")

    monkeypatch.setattr(ctx.urllib.request, "urlopen", fake_urlopen)

    result = ctx.tool_fetch_url({"url": "https://example.com/doc.txt", "user_provided": True})

    assert result["ok"] is True
    assert result["text"] == "hello"
