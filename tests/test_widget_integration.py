"""Widget smoke tests (Issues 20, 21) — the built widget bundle is served,
the dev-proxy /widget-demo prefix overlap stays fixed, and a non-ASCII
Authorization value is rejected at the HTTP layer exactly the way a raw
(unsanitized) data-api-key would be, which is why the widget must sanitize
before ever setting the header.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from api.core.auth import create_access_token
from api.core.config import get_settings
from api.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_widget_js_endpoint_returns_200():
    with TestClient(app) as client:
        res = client.get("/widget/chatbot.js")
    assert res.status_code == 200
    assert "javascript" in res.headers.get("content-type", "")


def test_widget_mount_does_not_claim_widget_demo():
    """The backend's StaticFiles mount is registered at the exact prefix
    "/widget" — Starlette's Mount matches "/widget-demo" too unless the
    dev proxy in front of it uses a trailing-slash (or otherwise exact)
    pattern (Issue 20). This confirms the failure mode the vite fix guards
    against: if a bare "/widget" proxy rule ever forwarded here, the
    backend has no route for it and would 404."""
    with TestClient(app) as client:
        res = client.get("/widget-demo")
    assert res.status_code == 404


def test_vite_proxy_widget_rule_excludes_widget_demo():
    """Static check on the fix itself: the dev proxy's widget rule must not
    be a bare "/widget" prefix (which greedily matches "/widget-demo")."""
    config_text = (REPO_ROOT / "frontend" / "vite.config.ts").read_text()
    assert '"/widget/"' in config_text, (
        "expected a trailing-slash (or otherwise exact) /widget proxy rule"
    )
    assert '"/widget":' not in config_text, (
        "a bare /widget proxy rule would swallow /widget-demo (Issue 20)"
    )


def test_ascii_authorization_header_reaches_server():
    """A well-formed ASCII bearer token must reach the server and get a
    normal auth response — not a header-encoding error."""
    settings = get_settings()
    token = create_access_token("some-user-id", settings)
    with TestClient(app) as client:
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    # Token is well-formed but the user doesn't exist — a clean 401, not a crash.
    assert res.status_code == 401


def test_non_ascii_authorization_header_is_rejected_at_http_layer():
    """This is exactly why the widget must sanitize data-api-key before
    ever using it as a header value (Issue 21): the HTTP layer itself
    refuses a non-ASCII value — httpx raises before a request is even
    sent. A widget that passed a raw, unsanitized key straight through
    would fail here, not with a clean server-side error."""
    non_ascii_token = "abc123…xyz"  # contains an ellipsis character
    # The encoding failure happens while building the header itself — no
    # network/transport involved, so this doesn't need the real app.
    with pytest.raises((UnicodeEncodeError, httpx.LocalProtocolError, ValueError)):
        httpx.Request(
            "GET", "http://testserver/api/auth/me",
            headers={"Authorization": f"Bearer {non_ascii_token}"},
        )


def test_sanitized_token_strips_non_ascii_before_use():
    """Mirrors widget/src/chat.ts's sanitizeHeaderValue: once non-ASCII
    characters are stripped client-side, the resulting token is a valid
    ASCII header value the server can process normally."""
    raw = "abc123…xyz"
    sanitized = "".join(ch for ch in raw if 0x20 <= ord(ch) <= 0x7E)
    assert sanitized == "abc123xyz"

    settings = get_settings()
    token = create_access_token(sanitized, settings)
    with TestClient(app) as client:
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401  # reaches the server cleanly; user just doesn't exist
