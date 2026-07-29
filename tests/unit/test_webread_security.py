from __future__ import annotations

import socket
import urllib.request

import pytest

import webread


def _dns_result(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/internal",
        "file:///etc/passwd",
        "https://user:password@example.com/",
    ],
)
def test_fetch_guard_rejects_local_non_web_and_credential_targets(url):
    with pytest.raises(ValueError):
        webread.assert_public_http_url(url)


def test_fetch_guard_rejects_a_hostname_resolving_to_private_ip(monkeypatch):
    monkeypatch.setattr(
        webread.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _dns_result("10.20.30.40"),
    )

    with pytest.raises(ValueError, match="Private"):
        webread.assert_public_http_url("https://attacker.example/document")


def test_fetch_guard_accepts_a_hostname_resolving_only_to_public_ips(monkeypatch):
    monkeypatch.setattr(
        webread.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _dns_result("93.184.216.34"),
    )

    webread.assert_public_http_url("https://example.com/document")


def test_redirect_guard_rechecks_the_destination():
    handler = webread._PublicOnlyRedirectHandler()
    request = urllib.request.Request("https://example.com/")

    with pytest.raises(ValueError):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data/",
        )


def test_mcp_fetch_rejects_private_targets_before_opening_them(ctx):
    with pytest.raises(ctx.McpError, match="not allowed"):
        ctx.tool_fetch_url({"url": "http://127.0.0.1:5002/api/team"})
