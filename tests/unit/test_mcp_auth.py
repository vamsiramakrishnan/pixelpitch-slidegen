"""OAuth audience, principal, expiry and request-isolation checks."""

import asyncio
import time

import httpx
import pytest

from app.mcp_auth import GoogleTokenVerifier, identity_from_tokeninfo

CLIENT = "123-pixelpitch.apps.googleusercontent.com"
DOMAINS = frozenset({"example.com"})


def claims(**changes):
    return {
        "aud": CLIENT,
        "azp": CLIENT,
        "sub": "user-123",
        "scope": "openid https://www.googleapis.com/auth/userinfo.email",
        "expires_in": "3600",
        "exp": str(int(time.time()) + 3600),
        "email": "person@example.com",
        "email_verified": "true",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "another-client"},
        {"azp": "another-client"},
        {"expires_in": "0"},
        {"exp": "1"},
        {"scope": "email"},
        {"email": "person@other.example.com"},
        {"email_verified": "false"},
        {"sub": ""},
        {"sub": None},
    ],
)
def test_untrusted_or_expired_identity_is_rejected(changes):
    with pytest.raises(PermissionError):
        identity_from_tokeninfo(claims(**changes), CLIENT, DOMAINS)


def test_subject_not_email_is_the_stable_owner():
    a = identity_from_tokeninfo(claims(), CLIENT, DOMAINS)
    assert (
        a.owner
        == identity_from_tokeninfo(
            claims(email="renamed@example.com"), CLIENT, DOMAINS
        ).owner
    )
    assert (
        a.owner
        != identity_from_tokeninfo(claims(sub="different-user"), CLIENT, DOMAINS).owner
    )
    assert len(a.owner) == 64 and a.ttl <= 60


@pytest.mark.asyncio
async def test_tokens_are_not_put_in_urls_and_only_success_is_cached():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST" and not request.url.query
        return httpx.Response(200, json=claims())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = GoogleTokenVerifier(CLIENT, DOMAINS, client)
        assert await verifier.verify("secret-access-token") == await verifier.verify(
            "secret-access-token"
        )
        assert len(requests) == 1
        assert "secret-access-token" not in repr(verifier.cache)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,kind", [(400, PermissionError), (503, RuntimeError)])
async def test_failed_auth_checks_are_not_cached(status, kind):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "denied"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = GoogleTokenVerifier(CLIENT, DOMAINS, client)
        for _ in range(2):
            with pytest.raises(kind):
                await verifier.verify("invalid")
        assert len(requests) == 2 and not verifier.cache


@pytest.mark.asyncio
async def test_polling_a_cached_user_does_not_wait_for_another_users_sign_in():
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        if b"slow" in request.content:
            started.set()
            await release.wait()
        return httpx.Response(200, json=claims())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = GoogleTokenVerifier(CLIENT, DOMAINS, client)
        cached = await verifier.verify("fast")
        pending = asyncio.create_task(verifier.verify("slow"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            assert await asyncio.wait_for(verifier.verify("fast"), 0.1) == cached
        finally:
            release.set()
            await pending
