"""Pilot auth: shared bearer token; disabled when SIGNOFF_TOKEN unset.

UI uses a signed-value cookie (the token itself) set at /ui/login.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Request, WebSocket


def token_required() -> bool:
    return bool(os.environ.get("SIGNOFF_TOKEN"))


def expected_token() -> str | None:
    return os.environ.get("SIGNOFF_TOKEN")


def _extract(request_like) -> str | None:
    auth = request_like.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # UI cookie fallback
    return request_like.cookies.get("signoff_session")


def check_request(request: Request | WebSocket) -> None:
    """Raise 401 when auth is enabled and the request lacks a valid token."""
    if not token_required():
        return
    got = _extract(request)
    if not got or got != expected_token():
        raise HTTPException(401, "authentication required")
