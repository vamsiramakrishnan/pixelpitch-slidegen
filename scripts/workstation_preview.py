"""Authorize one workstation port and measure the real A2A intake over its proxy.

Uses keyless service-account impersonation. Tokens stay in memory and never
appear in report files, URLs, browser storage, or command arguments.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import google.auth
import httpx
import requests
from google.auth import impersonated_credentials
from google.auth.transport.requests import AuthorizedSession

ROOT = Path(__file__).resolve().parents[1]
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
EXTENSION = "https://a2ui.org/a2a-extension/a2ui/v0.9"
CATALOG = "gemini_enterprise_composite_catalog.json"
PROMPT = "Prepare an editable deck brief for a three-slide customer growth review."
RESOURCE = re.compile(
    r"projects/[^/]+/locations/[^/]+/workstationClusters/[^/]+/"
    r"workstationConfigs/[^/]+/workstations/[^/]+"
)


def port_number(value: str) -> int:
    port = int(value)
    if not 10000 < port <= 65535:
        raise argparse.ArgumentTypeError("preview port must be 10001..65535")
    return port


def port_policy(policy: dict, account: str, port: int) -> dict:
    """Preserve the etag and every existing binding; add only this exact grant."""
    updated = copy.deepcopy(policy)
    binding = {
        "role": "roles/workstations.user",
        "members": [f"serviceAccount:{account}"],
        "condition": {
            "title": f"pixelpitch-preview-{port}",
            "expression": f"destination.port == {port}",
        },
    }
    if binding not in updated.setdefault("bindings", []):
        updated["bindings"].append(binding)
    updated["version"] = 3
    return updated


def api_session(account: str | None = None) -> AuthorizedSession:
    credentials, _ = google.auth.default(scopes=[SCOPE])
    if account:
        credentials = impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=account,
            target_scopes=[SCOPE],
            lifetime=900,
        )
    return AuthorizedSession(credentials)


def authorize(resource: str, account: str, port: int) -> None:
    endpoint = f"https://workstations.googleapis.com/v1/{resource}"
    with api_session() as session:
        response = session.get(
            endpoint + ":getIamPolicy",
            params={"options.requestedPolicyVersion": 3},
            timeout=30,
        )
        response.raise_for_status()
        policy = port_policy(response.json(), account, port)
        response = session.post(
            endpoint + ":setIamPolicy", json={"policy": policy}, timeout=30
        )
        response.raise_for_status()
    print(f"Authorized {account} for workstation port {port} only.")


def proxy_token(resource: str, account: str | None, port: int) -> str:
    with api_session(account) as session:
        response = session.post(
            f"https://workstations.googleapis.com/v1/{resource}:generateAccessToken",
            json={"port": port, "ttl": "900s"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["accessToken"]


def same_origin(base: str, target: str) -> bool:
    left, right = urlsplit(base), urlsplit(target)
    return (left.scheme, left.netloc) == (right.scheme, right.netloc)


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def intake(session: requests.Session, base: str) -> dict:
    """No generate action: drain a real editable-brief turn, not a deck job."""
    body = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/stream",
        "params": {
            "message": {
                "messageId": uuid.uuid4().hex,
                "role": "user",
                "parts": [{"kind": "text", "text": PROMPT}],
            }
        },
    }
    started = time.perf_counter()
    metrics = {"events": 0, "surface_messages": 0, "completed": False}
    with httpx.stream(
        "POST",
        base + "/a2a/app",
        json=body,
        headers={**session.headers, "X-A2A-Extensions": EXTENSION},
        timeout=httpx.Timeout(90, connect=15),
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise RuntimeError(
                f"A2A returned HTTP {response.status_code}, not a stream"
            )
        metrics["headers_ms"] = round((time.perf_counter() - started) * 1000, 1)
        if EXTENSION not in response.headers.get("X-A2A-Extensions", ""):
            raise RuntimeError("A2UI extension was not activated on the response")
        for line in response.iter_lines():
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            if elapsed > 90000:
                raise RuntimeError("Intake exceeded the 90-second measurement budget")
            if not line.startswith("data:"):
                continue
            envelope = json.loads(line[5:].strip())
            if envelope.get("error"):
                raise RuntimeError(f"A2A error: {envelope['error']}")
            result = envelope.get("result", {})
            metrics["events"] += 1
            metrics.setdefault("first_event_ms", elapsed)
            nodes = list(dictionaries(result))
            if any(
                node.get("role") == "agent"
                and any(
                    part.get("kind") == "text" and part.get("text")
                    for part in node.get("parts", [])
                )
                for node in nodes
            ):
                metrics.setdefault("first_text_ms", elapsed)
            for node in nodes:
                if node.get("kind") != "data":
                    continue
                data = node.get("data", {})
                if "createSurface" in data:
                    if CATALOG not in data["createSurface"].get("catalogId", ""):
                        raise RuntimeError("A2UI surface uses the wrong catalog")
                    metrics.setdefault("first_surface_ms", elapsed)
                    metrics["surface_messages"] += 1
                if data.get("updateComponents", {}).get("components"):
                    metrics.setdefault("first_components_ms", elapsed)
            if result.get("final"):
                metrics["completed"] = (
                    result.get("status", {}).get("state") == "completed"
                )
                break
        metrics["complete_ms"] = round((time.perf_counter() - started) * 1000, 1)
    if not metrics["completed"] or "first_components_ms" not in metrics:
        raise RuntimeError(f"No complete editable A2UI brief: {metrics}")
    return metrics


def assets(session: requests.Session, base: str) -> dict:
    started = time.perf_counter()
    index = base + "/dev-ui/"
    response = session.get(index, timeout=30, allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200 or "text/html" not in response.headers.get(
        "Content-Type", ""
    ):
        raise RuntimeError("Dev UI did not return HTML directly")
    urls = {
        urljoin(index, match.group(1))
        for match in re.finditer(r'(?:src|href)="([^"]+)"', response.text)
    }
    checked = 0
    for url in sorted(urls):
        if not same_origin(base, url):
            continue
        asset = session.get(url, timeout=30, allow_redirects=False)
        if asset.status_code != 200:
            raise RuntimeError(
                f"Dev UI asset {urlsplit(url).path}: {asset.status_code}"
            )
        checked += 1
    card = session.get(base + "/a2a/app/.well-known/agent-card.json", timeout=30)
    card.raise_for_status()
    if CATALOG not in json.dumps(card.json()):
        raise RuntimeError(
            "Agent card is missing the Gemini Enterprise composite catalog"
        )
    return {
        "assets_checked": checked,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def browser_probe(base: str, token: str | None, directory: Path) -> dict:
    """Inspect the actual Dev UI. This is not Gemini Enterprise rendering proof."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})

        def authenticate(route):
            headers = dict(route.request.headers)
            if token and same_origin(base, route.request.url):
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers.pop("authorization", None)
            route.continue_(headers=headers)

        context.route("**/*", authenticate)
        page = context.new_page()
        errors, failed = [], []
        page.on("pageerror", lambda error: errors.append(str(error)[:300]))
        page.on(
            "response",
            lambda response: (
                failed.append(
                    {"path": urlsplit(response.url).path, "status": response.status}
                )
                if same_origin(base, response.url) and response.status >= 400
                else None
            ),
        )
        page.goto(base + "/dev-ui/?app=app", wait_until="networkidle", timeout=60000)
        page.screenshot(path=str(directory / "dev-ui.png"), full_page=True)
        report = {
            "url": page.url,
            "title": page.title(),
            "errors": errors,
            "failed_responses": failed,
            "paint": page.evaluate(
                "performance.getEntriesByType('paint').map(p => ({name:p.name, ms:p.startTime}))"
            ),
            "body_excerpt": page.locator("body").inner_text()[:700],
        }
        browser.close()
    return report


def probe(args) -> dict:
    base = f"https://{args.port}-{args.host}"
    directory = ROOT / ".tmp" / "workstation-preview" / str(args.port)
    directory.mkdir(parents=True, exist_ok=True)
    account = None if args.current_identity else args.service_account
    token = proxy_token(args.workstation, account, args.port)
    with requests.get(
        base + "/list-apps", timeout=30, allow_redirects=False
    ) as response:
        unauthenticated = response.status_code
    if unauthenticated == 200:
        raise RuntimeError("Proxy unexpectedly allows unauthenticated app access")
    denied_port = None
    if account:
        denied_port = 18091 if args.port != 18091 else 18092
        try:
            proxy_token(args.workstation, account, denied_port)
        except requests.HTTPError as error:
            denied = error.response.status_code == 403
        else:
            denied = False
        if not denied:
            raise RuntimeError(
                "Caller can access an ungranted port, or denial was not HTTP 403"
            )
    report = {
        "proxy": base,
        "service_account": account,
        "identity_mode": "impersonated" if account else "existing ADC identity",
        "unauthenticated_status": unauthenticated,
        "other_port_denied": denied_port,
        "runs": {},
    }
    for label, origin in (
        ("localhost", f"http://127.0.0.1:{args.port}"),
        ("proxy", base),
    ):
        with requests.Session() as session:
            if label == "proxy":
                session.headers["Authorization"] = f"Bearer {token}"
            report["runs"][label] = {
                "static": assets(session, origin),
                "intake": [intake(session, origin) for _ in range(args.runs)],
            }
    if args.browser:
        report["browser"] = browser_probe(base, token, directory)
    path = directory / "report.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Evidence: {path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["authorize", "probe"])
    parser.add_argument("--port", type=port_number, default=18090)
    parser.add_argument(
        "--workstation",
        default=os.environ.get("CLOUD_WORKSTATIONS_WORKSTATION_RESOURCE_NAME", ""),
    )
    parser.add_argument("--host", default=os.environ.get("WEB_HOST", ""))
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--service-account")
    identity.add_argument("--current-identity", action="store_true")
    parser.add_argument("--runs", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    if not RESOURCE.fullmatch(args.workstation):
        parser.error("--workstation must name one complete workstation resource")
    if args.service_account and not re.fullmatch(
        r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", args.service_account
    ):
        parser.error("--service-account must be a service account email")
    if args.command == "authorize":
        if not args.service_account:
            parser.error("authorize requires --service-account")
        authorize(args.workstation, args.service_account, args.port)
    else:
        if not re.fullmatch(r"[a-z0-9.-]+\.cloudworkstations\.dev", args.host):
            parser.error("--host must be the workstation's cloudworkstations.dev host")
        try:
            probe(args)
        except requests.HTTPError as error:
            try:
                message = error.response.json().get("error", {}).get("message", "")
            except ValueError:
                message = "The server rejected the request."
            raise SystemExit(f"HTTP {error.response.status_code}: {message}") from None


if __name__ == "__main__":
    main()
