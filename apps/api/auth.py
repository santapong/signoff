"""Shared-token pilot authentication with opaque signed cookies and CSRF."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request, WebSocket


def token_required() -> bool:
    return bool(expected_token())


def expected_token() -> str | None:
    return os.environ.get("SIGNOFF_TOKEN")


def session_cookie() -> str:
    nonce = secrets.token_urlsafe(24)
    signature = hmac.new(
        (expected_token() or "local-development").encode(), nonce.encode(), hashlib.sha256
    ).hexdigest()
    return f"{nonce}.{signature}"


def _cookie_valid(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    nonce, signature = value.rsplit(".", 1)
    expected = hmac.new(
        (expected_token() or "local-development").encode(), nonce.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def bearer_valid(request_like) -> bool:
    auth = request_like.headers.get("authorization", "")
    token = expected_token()
    return bool(
        token
        and auth.lower().startswith("bearer ")
        and hmac.compare_digest(auth[7:].strip(), token)
    )


def check_request(request: Request | WebSocket) -> None:
    if token_required() and not (
        bearer_valid(request) or _cookie_valid(request.cookies.get("signoff_session"))
    ):
        raise HTTPException(401, "authentication required")


def check_csrf(request: Request, submitted: str | None) -> None:
    """Bearer clients do not use browser cookie authority."""
    if bearer_valid(request) or not request.cookies.get("signoff_session"):
        return
    cookie = request.cookies.get("signoff_csrf", "")
    if not cookie or not submitted or not hmac.compare_digest(cookie, submitted):
        raise HTTPException(403, "CSRF token required for cookie-authenticated mutation")


def check_websocket(ws: WebSocket) -> None:
    check_request(ws)
    origin = ws.headers.get("origin")
    if origin:
        from urllib.parse import urlparse

        if urlparse(origin).netloc != ws.headers.get("host"):
            raise HTTPException(403, "WebSocket origin is not allowed")
