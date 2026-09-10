"""Verify the user's Google OAuth token independently of Cloud Run IAM."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

current_owner: ContextVar[str] = ContextVar("pixelpitch_mcp_owner", default="local")


@dataclass(frozen=True)
class Identity:
    owner: str
    ttl: float


def identity_from_tokeninfo(
    info: dict, client_id: str, domains: frozenset[str]
) -> Identity:
    """Only Google's authenticated tokeninfo response is accepted here."""
    try:
        ttl = min(float(info["expires_in"]), float(info["exp"]) - time.time(), 60.0)
        subject = info["sub"]
        email = info["email"].lower()
        valid = (
            info.get("aud") == client_id
            and info.get("azp") == client_id
            and isinstance(subject, str)
            and 0 < len(subject) <= 255
            and info.get("email_verified") in (True, "true")
            and "openid" in info.get("scope", "").split()
            and email.rpartition("@")[2] in domains
            and ttl > 0
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise PermissionError("Sign in with an approved account to use Pixelpitch.")
    return Identity(
        hashlib.sha256(f"https://accounts.google.com|{subject}".encode()).hexdigest(),
        ttl,
    )


class GoogleTokenVerifier:
    def __init__(
        self, client_id: str, domains: frozenset[str], client: httpx.AsyncClient
    ):
        self.client_id, self.domains, self.client = client_id, domains, client
        self.cache: OrderedDict[str, tuple[float, Identity]] = OrderedDict()
        self.lock = asyncio.Lock()

    async def verify(self, token: str) -> Identity:
        key = hashlib.sha256(token.encode()).hexdigest()
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            self.cache.move_to_end(key)
            return cached[1]
        # Serialize cache misses so opening/polling the same widget shares one check.
        async with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] > time.monotonic():
                self.cache.move_to_end(key)
                return cached[1]
            try:
                # POST keeps the bearer out of URLs, proxies and ordinary HTTP logs.
                response = await self.client.post(
                    "https://oauth2.googleapis.com/tokeninfo",
                    data={"access_token": token},
                    timeout=5,
                )
                if response.status_code in (400, 401, 403):
                    raise PermissionError(
                        "Your sign-in expired. Reconnect Pixelpitch from chat."
                    )
                response.raise_for_status()
                identity = identity_from_tokeninfo(
                    response.json(), self.client_id, self.domains
                )
            except (httpx.HTTPError, ValueError) as error:
                raise RuntimeError(
                    "Sign-in verification is temporarily unavailable. Please retry."
                ) from error
            self.cache[key] = (time.monotonic() + identity.ttl, identity)
            self.cache.move_to_end(key)
            while len(self.cache) > 256:
                self.cache.popitem(last=False)
            return identity


class UserAuthMiddleware:
    def __init__(self, app, verifier: GoogleTokenVerifier):
        self.app, self.verifier = app, verifier

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        request = Request(scope)
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 8192:
            response = JSONResponse(
                {"error": "Sign in to use Pixelpitch."},
                401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        try:
            identity = await self.verifier.verify(token)
        except (PermissionError, RuntimeError) as error:
            status = 401 if isinstance(error, PermissionError) else 503
            response = JSONResponse(
                {"error": str(error)},
                status,
                headers={
                    "Cache-Control": "no-store",
                    **({"WWW-Authenticate": "Bearer"} if status == 401 else {}),
                },
            )
            return await response(scope, receive, send)
        context = current_owner.set(identity.owner)
        try:
            await self.app(scope, receive, send)
        finally:
            current_owner.reset(context)
