#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Resolve deployment configuration and run the steps that need it.

A slidegen deployment is a chain, and the awkward part is that most of the
chain's inputs are outputs of an earlier link. The Discovery Engine service
agent's address contains the project *number*, the agent's env needs the
renderer's assigned URL, and the Gemini Enterprise registration needs the
agent's assigned URL. Every one of those is knowable only after something
else exists, so an operator following a written runbook spends the deploy
copying strings between terminal windows and gets one of them wrong.

The split this module enforces: ``deploy.env`` holds *only* what a human
knows, and everything else is looked up live at the moment it is needed.
Derived values are never written down, because a written-down project number
or service URL is a value that goes stale silently and fails somewhere far
from the edit.

``mise.toml`` is the operator-facing surface. This is the part of it that
could not be a one-line ``gcloud`` call.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "deploy.env"
EXAMPLE_FILE = ROOT / "deploy.env.example"

RENDERER_SERVICE = "slidegen-renderer"
AGENT_SERVICE = "slidegen-agent"
RENDERER_SECRET = "slidegen-renderer-key"

# Bucket names are the one field here with a naming rule strict enough to
# reject something that looks fine in a config file. The Terraform this
# replaced interpolated a project name containing a slash straight into a
# bucket name, so it could never have applied. Checked, not assumed.
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_GE_APP_RE = re.compile(
    r"^projects/[^/]+/locations/[^/]+/collections/[^/]+/engines/[^/]+$"
)


@dataclass(frozen=True)
class Config:
    """The values a human supplies. Nothing derived lives here.

    Anything that can be looked up from Google Cloud is looked up, every
    time, by the functions below. Caching a project number or a service URL
    in this file is how these deployments rot.
    """

    project_id: str
    region: str = "us-central1"
    # Where the models are served from. Distinct from `region`, which is
    # where the containers run; Vertex answers "global" for these models.
    model_location: str = "global"
    deck_bucket: str = ""
    artifact_repo: str = "pixelpitch-agents"
    ge_app_id: str = ""
    deployment_mode: str = "a2a"
    authoring_model: str = ""
    template_preparation: str = "automatic"
    ge_agent_id: str = ""
    mcp_oauth_client_id: str = ""
    mcp_allowed_email_domains: str = ""

    def __post_init__(self) -> None:
        if not self.deck_bucket:
            object.__setattr__(self, "deck_bucket", default_bucket(self.project_id))

    @property
    def image(self) -> str:
        host = f"{self.region}-docker.pkg.dev"
        return f"{host}/{self.project_id}/{self.artifact_repo}/{RENDERER_SERVICE}"


def default_bucket(project_id: str) -> str:
    """Bucket names are globally unique, so the project id is the only
    prefix that is available by construction."""
    return f"{project_id}-slidegen-decks"


def agent_card_url(agent_url: str) -> str:
    """Where Gemini Enterprise reads the A2A card.

    The path is fixed by the ADK A2A mount, not by us. Registering the bare
    service URL produces an agent that resolves but renders nothing.
    """
    return f"{agent_url.rstrip('/')}/a2a/app/.well-known/agent-card.json"


def discovery_engine_agent(project_number: str) -> str:
    """The Google-managed identity Gemini Enterprise calls the agent with.

    It is per-project and contains the project *number*, not the id, which
    is the single value operators most often get wrong by hand.
    """
    return f"service-{project_number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"


def validate(cfg: Config) -> list[str]:
    """Every reason this config cannot deploy, not just the first one.

    Returning the whole list matters in Cloud Shell, where each round trip
    to fix one field is a context switch for the operator.
    """
    problems: list[str] = []
    if not _PROJECT_RE.match(cfg.project_id):
        problems.append(
            f"PROJECT_ID {cfg.project_id!r} is not a valid Google Cloud project id "
            "(6-30 chars, lowercase letter first, letters/digits/hyphens)."
        )
    if not re.fullmatch(r"[a-z]+-[a-z]+[0-9]", cfg.region):
        problems.append("REGION must be set, for example us-central1.")
    if not re.fullmatch(r"global|[a-z]+-[a-z]+[0-9]", cfg.model_location):
        problems.append("MODEL_LOCATION must be global or a Google Cloud region.")
    if cfg.deployment_mode not in ("a2a", "mcp", "both"):
        problems.append("DEPLOYMENT_MODE must be a2a, mcp, or both.")
    if cfg.template_preparation not in ("automatic", "on-demand"):
        problems.append("TEMPLATE_PREPARATION must be automatic or on-demand.")
    if cfg.authoring_model and not re.fullmatch(
        r"[a-zA-Z0-9._-]+", cfg.authoring_model
    ):
        problems.append(
            "AUTHORING_MODEL must be a model ID, not shell syntax or a URL."
        )
    if cfg.ge_agent_id and not re.fullmatch(r"[a-zA-Z0-9_-]+", cfg.ge_agent_id):
        problems.append(
            "GE_AGENT_ID must be the existing agent ID, not its full resource path."
        )
    if cfg.deployment_mode in ("mcp", "both"):
        if not re.fullmatch(
            r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", cfg.mcp_oauth_client_id
        ):
            problems.append(
                "MCP_OAUTH_CLIENT_ID must be an approved Google Web OAuth client ID (never the secret)."
            )
        domains = cfg.mcp_allowed_email_domains.split(",")
        if not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", domain)
            for domain in domains
        ):
            problems.append(
                "MCP_ALLOWED_EMAIL_DOMAINS must contain exact lowercase domains, separated by commas."
            )
    if not _BUCKET_RE.match(cfg.deck_bucket):
        problems.append(
            f"DECK_BUCKET {cfg.deck_bucket!r} is not a valid Cloud Storage bucket "
            "name (3-63 chars, lowercase letters, digits, dots, hyphens, "
            "underscores; must start and end alphanumeric)."
        )
    if not re.match(r"^[a-z][a-z0-9-]{0,62}$", cfg.artifact_repo):
        problems.append(
            f"ARTIFACT_REPO {cfg.artifact_repo!r} is not a valid Artifact Registry "
            "repository name."
        )
    if cfg.ge_app_id and not _GE_APP_RE.match(cfg.ge_app_id):
        problems.append(
            f"GE_APP_ID {cfg.ge_app_id!r} must be a full resource name like "
            "projects/123/locations/global/collections/default_collection/"
            "engines/my-app_1234567890."
        )
    return problems


def parse_env_file(text: str) -> dict[str, str]:
    """Read the ``KEY=value`` subset that a config file needs.

    Deliberately not a shell parser. A config file that needs command
    substitution to be understood is a config file an operator cannot audit
    before running it against their production project.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def load(path: Path = CONFIG_FILE, environ: Mapping[str, str] | None = None) -> Config:
    """Config file first, real environment second.

    The environment wins so that CI and a `PROJECT_ID=x mise run deploy`
    one-off do not require editing a tracked-adjacent file.
    """
    environ = os.environ if environ is None else environ
    values: dict[str, str] = {}
    if path.is_file():
        values.update(parse_env_file(path.read_text(encoding="utf-8")))
    for key in (
        "PROJECT_ID",
        "REGION",
        "MODEL_LOCATION",
        "DECK_BUCKET",
        "ARTIFACT_REPO",
        "GE_APP_ID",
        "DEPLOYMENT_MODE",
        "AUTHORING_MODEL",
        "TEMPLATE_PREPARATION",
        "GE_AGENT_ID",
        "MCP_OAUTH_CLIENT_ID",
        "MCP_ALLOWED_EMAIL_DOMAINS",
    ):
        if environ.get(key):
            values[key] = environ[key]

    if not values.get("PROJECT_ID"):
        die(
            f"No PROJECT_ID. Run `mise run configure` to create {path.name}, "
            f"or copy {EXAMPLE_FILE.name} to {path.name} and edit it."
        )
    return Config(
        project_id=values["PROJECT_ID"],
        region=values.get("REGION") or "us-central1",
        model_location=values.get("MODEL_LOCATION") or "global",
        deck_bucket=values.get("DECK_BUCKET", ""),
        artifact_repo=values.get("ARTIFACT_REPO") or "pixelpitch-agents",
        ge_app_id=values.get("GE_APP_ID", ""),
        deployment_mode=values.get("DEPLOYMENT_MODE") or "a2a",
        authoring_model=values.get("AUTHORING_MODEL", ""),
        template_preparation=values.get("TEMPLATE_PREPARATION") or "automatic",
        ge_agent_id=values.get("GE_AGENT_ID", ""),
        mcp_oauth_client_id=values.get("MCP_OAUTH_CLIENT_ID", ""),
        mcp_allowed_email_domains=values.get("MCP_ALLOWED_EMAIL_DOMAINS", ""),
    )


def render_agent_env(cfg: Config, renderer_url: str) -> str:
    """The ``.env`` that ``agents-cli deploy`` turns into ``--update-env-vars``.

    Generated rather than hand-maintained because ``SLIDEGEN_RENDERER_URL``
    is assigned by Cloud Run. A stale value here is the failure mode where
    the agent deploys clean and every deck request 404s.
    """
    return "\n".join(
        [
            "# Generated by `mise run deploy`. Edits are overwritten.",
            "# Human-supplied values live in deploy.env.",
            "GOOGLE_GENAI_USE_VERTEXAI=true",
            f"GOOGLE_CLOUD_PROJECT={cfg.project_id}",
            f"GOOGLE_CLOUD_LOCATION={cfg.model_location}",
            f"SLIDEGEN_RENDERER_URL={renderer_url}",
            f"SLIDEGEN_GCS_BUCKET={cfg.deck_bucket}",
            *(
                [f"SLIDEGEN_AUTHORING_MODEL={cfg.authoring_model}"]
                if cfg.authoring_model
                else []
            ),
            "",
        ]
    )


# ---------------------------------------------------------------- shell out


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(cmd: list[str], *, capture: bool = True, check: bool = True) -> str:
    proc = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        cwd=ROOT,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        die(f"`{' '.join(cmd[:3])} ...` failed: {detail[-800:]}")
    return (proc.stdout or "").strip()


def project_number(project_id: str) -> str:
    return run(
        [
            "gcloud",
            "projects",
            "describe",
            project_id,
            "--format=value(projectNumber)",
        ]
    )


def service_url(name: str, cfg: Config) -> str:
    """The assigned URL, or empty string when the service does not exist.

    Absence is an ordinary state here (it is the state before the first
    deploy), so it is a return value rather than an error.
    """
    proc = subprocess.run(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            name,
            f"--region={cfg.region}",
            f"--project={cfg.project_id}",
            "--format=value(status.url)",
        ],
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def require_service_url(name: str, cfg: Config) -> str:
    url = service_url(name, cfg)
    if not url:
        die(
            f"Cloud Run service {name!r} does not exist in {cfg.project_id}/"
            f"{cfg.region}. Run `mise run deploy` to create it."
        )
    return url


# ------------------------------------------------------------- subcommands


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load()
    problems = validate(cfg)
    if problems and not args.json:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "project_id": cfg.project_id,
                    "region": cfg.region,
                    "model_location": cfg.model_location,
                    "deck_bucket": cfg.deck_bucket,
                    "artifact_repo": cfg.artifact_repo,
                    "ge_app_id": cfg.ge_app_id,
                    "deployment_mode": cfg.deployment_mode,
                    "authoring_model": cfg.authoring_model,
                    "template_preparation": cfg.template_preparation,
                    "ge_agent_id": cfg.ge_agent_id,
                    "image": cfg.image,
                    "problems": problems,
                },
                indent=2,
            )
        )
        return 1 if problems else 0

    if args.shell:
        for key, value in (
            ("PROJECT_ID", cfg.project_id),
            ("REGION", cfg.region),
            ("MODEL_LOCATION", cfg.model_location),
            ("DECK_BUCKET", cfg.deck_bucket),
            ("ARTIFACT_REPO", cfg.artifact_repo),
            ("GE_APP_ID", cfg.ge_app_id),
            ("RENDERER_IMAGE", cfg.image),
            ("DEPLOYMENT_MODE", cfg.deployment_mode),
            ("AUTHORING_MODEL", cfg.authoring_model),
            ("TEMPLATE_PREPARATION", cfg.template_preparation),
        ):
            print(f"{key}={shlex.quote(value)}")
        return 0

    print(f"  project        {cfg.project_id}")
    print(f"  region         {cfg.region}")
    print(f"  model location {cfg.model_location}")
    print(f"  deck bucket    {cfg.deck_bucket}")
    print(f"  image          {cfg.image}")
    print(f"  GE app         {cfg.ge_app_id or '(not set; register will prompt)'}")
    print(f"  interfaces     {cfg.deployment_mode}")
    print(
        f"  authoring      {cfg.authoring_model or '(preserve existing override or code default)'}"
    )
    print(f"  templates      {cfg.template_preparation}")
    print(f"  reuse GE agent {cfg.ge_agent_id or '(match existing endpoint)'}")
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    """Write ``deploy.env`` by asking, defaulting to what gcloud already knows."""
    if CONFIG_FILE.is_file() and not args.force:
        print(f"{CONFIG_FILE.name} already exists. Re-run with --force to replace it.")
        print()
        return cmd_config(argparse.Namespace(json=False, shell=False))

    gcloud_project = ""
    if shutil.which("gcloud"):
        proc = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
        )
        candidate = proc.stdout.strip()
        if candidate and candidate != "(unset)":
            gcloud_project = candidate

    def ask(label: str, default: str) -> str:
        suffix = f" [{default}]" if default else ""
        try:
            reply = input(f"{label}{suffix}: ").strip()
        except EOFError:
            reply = ""
        return reply or default

    print("Pixelpitch slidegen deployment configuration.")
    print("Press enter to accept each default.")
    print()
    project_id = ask("Google Cloud project id", gcloud_project)
    if not project_id:
        die("A project id is required.")
    region = ask("Cloud Run region", "us-central1")
    model_location = ask("Vertex model location", "global")
    mode = ask("Interfaces: a2a, mcp, or both", "a2a")
    authoring_model = ask("Authoring model ID (blank preserves existing/default)", "")
    preparation = ask("Template preparation: automatic or on-demand", "automatic")
    deck_bucket = ask("Bucket for generated decks", default_bucket(project_id))

    print()
    print("Gemini Enterprise app. Leave blank to choose interactively later,")
    print("or list the apps in this project with:")
    print("  mise run ge-apps")
    ge_app_id = ask("Gemini Enterprise app resource name", "")
    ge_agent_id = (
        ask("Existing A2A registration ID (blank matches endpoint)", "")
        if mode != "mcp"
        else ""
    )
    client_id = domains = ""
    if mode in ("mcp", "both"):
        print(
            "MCP requires an approved Google Web OAuth client. Do not enter its secret."
        )
        client_id = ask("MCP OAuth client ID", "")
        domains = ask("Allowed user email domains, comma-separated", "")

    cfg = Config(
        project_id=project_id,
        region=region,
        deck_bucket=deck_bucket,
        ge_app_id=ge_app_id,
        model_location=model_location,
        deployment_mode=mode,
        authoring_model=authoring_model,
        template_preparation=preparation,
        ge_agent_id=ge_agent_id,
        mcp_oauth_client_id=client_id,
        mcp_allowed_email_domains=domains,
    )
    problems = validate(cfg)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    CONFIG_FILE.write_text(
        "\n".join(
            [
                "# Pixelpitch slidegen deployment configuration.",
                "# Only values a human knows belong here. Project numbers and",
                "# service URLs are looked up live at deploy time.",
                f"PROJECT_ID={cfg.project_id}",
                f"REGION={cfg.region}",
                f"MODEL_LOCATION={cfg.model_location}",
                f"DECK_BUCKET={cfg.deck_bucket}",
                f"ARTIFACT_REPO={cfg.artifact_repo}",
                f"GE_APP_ID={cfg.ge_app_id}",
                f"DEPLOYMENT_MODE={cfg.deployment_mode}",
                f"AUTHORING_MODEL={cfg.authoring_model}",
                f"TEMPLATE_PREPARATION={cfg.template_preparation}",
                f"GE_AGENT_ID={cfg.ge_agent_id}",
                f"MCP_OAUTH_CLIENT_ID={cfg.mcp_oauth_client_id}",
                f"MCP_ALLOWED_EMAIL_DOMAINS={cfg.mcp_allowed_email_domains}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    CONFIG_FILE.chmod(0o600)
    print()
    print(f"Wrote {CONFIG_FILE.name}.")
    print("Next: mise run deploy")
    return 0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


@dataclass
class Preflight:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", fix: str = "") -> None:
        self.checks.append(Check(name, ok, detail, fix))

    def report(self) -> int:
        width = max(len(c.name) for c in self.checks)
        for check in self.checks:
            mark = "PASS" if check.ok else "FAIL"
            print(f"  {mark}  {check.name.ljust(width)}  {check.detail}")
        failed = [c for c in self.checks if not c.ok]
        if failed:
            print()
            print("Fix these before deploying:")
            for check in failed:
                print(f"  - {check.name}: {check.fix}")
        return 1 if failed else 0


def cmd_preflight(args: argparse.Namespace) -> int:
    """Prove the environment can deploy before spending twenty minutes on it.

    Every check here corresponds to a failure that otherwise surfaces
    halfway through, after an image build, with an error that names an API
    rather than the thing the operator has to go and do.
    """
    cfg = load()
    pre = Preflight()

    problems = validate(cfg)
    pre.add(
        "config",
        not problems,
        cfg.project_id,
        "; ".join(problems) or "",
    )

    for tool, hint in (
        ("gcloud", "Cloud Shell has this. Locally: https://cloud.google.com/sdk"),
        ("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh"),
        ("terraform", "mise installs this; run `mise install`"),
    ):
        found = shutil.which(tool)
        pre.add(f"tool {tool}", bool(found), found or "not on PATH", hint)

    if not shutil.which("gcloud") or problems:
        return pre.report()

    account = ""
    proc = subprocess.run(
        ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        capture_output=True,
        text=True,
    )
    account = proc.stdout.strip()
    pre.add(
        "authenticated",
        bool(account),
        account or "no active account",
        "gcloud auth login && gcloud auth application-default login",
    )

    number = ""
    proc = subprocess.run(
        [
            "gcloud",
            "projects",
            "describe",
            cfg.project_id,
            "--format=value(projectNumber)",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        number = proc.stdout.strip()
    pre.add(
        "project access",
        bool(number),
        f"{cfg.project_id} ({number})" if number else "cannot describe project",
        f"Check the id, and that {account or 'your account'} has access to it.",
    )

    if number:
        proc = subprocess.run(
            [
                "gcloud",
                "billing",
                "projects",
                "describe",
                cfg.project_id,
                "--format=value(billingEnabled)",
            ],
            capture_output=True,
            text=True,
        )
        billing = proc.stdout.strip().lower()
        # A missing billing permission is not a missing billing account, and
        # telling an operator to enable billing they already have wastes a
        # support cycle. Only "False" is a real failure.
        pre.add(
            "billing",
            billing != "false",
            billing or "not readable (needs billing.viewer; not fatal)",
            "Link a billing account: "
            f"https://console.cloud.google.com/billing/linkedaccount?project={cfg.project_id}",
        )

    return pre.report()


def cmd_agent_env(args: argparse.Namespace) -> int:
    """Write the ``.env`` the agent deploy reads, from the live renderer URL."""
    cfg = load()
    renderer = require_service_url(RENDERER_SERVICE, cfg)
    target = ROOT / ".env"
    current = target.read_text() if target.exists() else ""
    if target.exists():
        backup = target.with_name(f".env.before-deploy-{time.time_ns()}")
        shutil.copy2(target, backup)
        backup.chmod(0o600)
    generated = render_agent_env(cfg, renderer)
    managed = parse_env_file(generated)
    retained = [
        line
        for line in current.splitlines()
        if not set(parse_env_file(line)) & managed.keys()
    ]
    target.write_text("\n".join(retained) + "\n" + generated, encoding="utf-8")
    target.chmod(0o600)
    print(f"  wrote {target.name} pointing the agent at {renderer}")
    return 0


def cmd_grant_invoker(args: argparse.Namespace) -> int:
    """Let Gemini Enterprise call the agent.

    The agent is deployed --no-allow-unauthenticated on purpose, so this
    binding is the whole reason a registered agent responds at all. The
    principal contains the project number, which is why it is computed here
    rather than written in a runbook.
    """
    cfg = load()
    agent = require_service_url(AGENT_SERVICE, cfg)
    ge_project = cfg.ge_app_id.split("/")[1] if cfg.ge_app_id else cfg.project_id
    principal = discovery_engine_agent(project_number(ge_project))
    run(
        [
            "gcloud",
            "run",
            "services",
            "add-iam-policy-binding",
            AGENT_SERVICE,
            f"--region={cfg.region}",
            f"--project={cfg.project_id}",
            f"--member=serviceAccount:{principal}",
            "--role=roles/run.invoker",
            "--quiet",
        ]
    )
    print(f"  {principal} can invoke {AGENT_SERVICE}")
    print(f"  agent card: {agent_card_url(agent)}")
    return 0


def _existing_ge_agents(cfg: Config) -> list[dict]:
    """Agents already registered on the target app, via the read-only API.

    Registration itself goes through agents-cli. This is only here to stop a
    second identical entry appearing next to the first, which is how a dead
    duplicate ends up in someone's Gemini Enterprise app with no way to
    disable it.
    """
    if not cfg.ge_app_id:
        return []
    token = run(["gcloud", "auth", "print-access-token"])
    url = f"https://discoveryengine.googleapis.com/v1alpha/{cfg.ge_app_id}/assistants/default_assistant/agents"
    agents, page_tokens = [], set()
    page_token = ""
    try:
        while True:
            page_url = (
                url
                + "?"
                + urllib.parse.urlencode({"pageSize": 100, "pageToken": page_token})
            )
            request = urllib.request.Request(
                page_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Goog-User-Project": cfg.project_id,
                },
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            agents.extend(payload.get("agents", []) or [])
            page_token = payload.get("nextPageToken", "")
            if not page_token:
                break
            if page_token in page_tokens:
                die("Registration listing repeated a page token; no agent was created.")
            page_tokens.add(page_token)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        die(
            "Could not inspect existing Gemini Enterprise registrations. Fix read access before registering; no duplicate was created."
        )
    return agents


def cmd_register(args: argparse.Namespace) -> int:
    """Register the deployed agent with Gemini Enterprise, via agents-cli.

    agents-cli owns this because the registration has two properties that
    are easy to get wrong by hand and silently produce an agent that renders
    plain text: it must be an a2a registration against an explicit agent
    card URL, and its authorizationConfig must stay empty so that Gemini
    Enterprise authenticates with IAM instead of starting an OAuth loop.
    """
    cfg = load()
    agent = require_service_url(AGENT_SERVICE, cfg)
    card = agent_card_url(agent)
    aliases = {
        agent.rstrip("/") + "/a2a/app",
        f"https://{AGENT_SERVICE}-{project_number(cfg.project_id)}.{cfg.region}.run.app/a2a/app",
    }
    existing_agents = _existing_ge_agents(cfg)
    if cfg.ge_agent_id and not any(
        item.get("name", "").split("/")[-1] == cfg.ge_agent_id
        for item in existing_agents
    ):
        die("GE_AGENT_ID was not found in this app. No registration was created.")
    for existing in existing_agents:
        definition = existing.get("a2aAgentDefinition") or {}
        raw = definition.get("jsonAgentCard") or {}
        try:
            inline = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            die(
                "An existing A2A card is malformed; inspect it before registering again."
            )
        selected = (
            existing.get("name", "").split("/")[-1] == cfg.ge_agent_id
            if cfg.ge_agent_id
            else False
        )
        matched = (
            definition.get("agentCardUri") == card
            or inline.get("url", "").rstrip("/") in aliases
        )
        if selected and not matched:
            die(
                "The selected registration points to another endpoint. Review/update that registration explicitly; no duplicate was created."
            )
        if matched:
            if existing.get("authorizationConfig"):
                die(
                    "The matching A2A registration has OAuth configuration. Review it explicitly before using the IAM-only deployment."
                )
            print(f"  reusing registration {existing.get('name')}; card unchanged")
            return 0

    cmd = [
        "uv",
        "run",
        "agents-cli",
        "publish",
        "gemini-enterprise",
        "--registration-type",
        "a2a",
        "--deployment-target",
        "cloud_run",
        "--project",
        cfg.project_id,
        "--agent-card-url",
        card,
    ]
    if cfg.ge_app_id:
        cmd += ["--gemini-enterprise-app-id", cfg.ge_app_id]
    else:
        # No app id configured, so let agents-cli do the asking. It can list
        # the caller's apps and validate the choice; a prompt we wrote here
        # would only be a worse copy of that.
        print("  no GE_APP_ID configured; agents-cli will prompt for the app")
        cmd += ["--interactive"]

    print(f"  registering {card}")
    completed = subprocess.run(cmd, cwd=ROOT)
    return completed.returncode


def deployment_steps(cfg: Config) -> list[str]:
    steps = ["preflight", "infra", "build", "deploy-renderer"]
    if cfg.deployment_mode in ("a2a", "both"):
        steps.append("deploy-agent")
    if cfg.deployment_mode in ("mcp", "both"):
        steps.append("deploy-mcp")
    if cfg.template_preparation == "automatic":
        steps.append("deploy-templates")
    if cfg.deployment_mode in ("a2a", "both"):
        steps += ["grant-invoker", "register", "verify"]
    if cfg.deployment_mode in ("mcp", "both"):
        steps.append("verify-mcp")
    return steps


def cmd_renderer_release(args: argparse.Namespace) -> int:
    cfg = load()
    path = ROOT / ".tmp" / "deploy" / "renderer-release.json"
    if args.command == "pin-renderer":
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", args.tag):
            die("Invalid image tag")
        digest = run(
            [
                "gcloud",
                "artifacts",
                "docker",
                "images",
                "describe",
                f"{cfg.image}:{args.tag}",
                "--project",
                cfg.project_id,
                "--format=value(image_summary.digest)",
            ]
        )
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            die("Cloud Build image did not return a SHA256 digest")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"image": f"{cfg.image}@{digest}"}) + "\n")
    if not path.exists():
        die("No pinned renderer image. Run mise run build first.")
    image = json.loads(path.read_text())["image"]
    if not re.fullmatch(re.escape(cfg.image) + r"@sha256:[a-f0-9]{64}", image):
        die(
            "Pinned renderer image belongs to another target. Rebuild for this project/region."
        )
    print(image)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = load()
    for problem in validate(cfg):
        print(f"error: {problem}", file=sys.stderr)
    if validate(cfg):
        return 1
    print(f"Target: {cfg.project_id}/{cfg.region}; interfaces: {cfg.deployment_mode}")
    print(" → ".join(deployment_steps(cfg)))
    print(
        "Creates billable Cloud Run, build, storage and registry resources; changes scoped IAM."
    )
    if cfg.deployment_mode != "a2a":
        print(
            "MCP adds Firestore, a private API and a continuously billed 4-CPU/8-GiB worker."
        )
        print(
            "The customer completes OAuth and MCP data-store setup in Gemini Enterprise."
        )
    print(
        "Keep Terraform state in persistent Cloud Shell storage and back it up before switching projects."
    )
    print(
        "This is a local execution plan, not a Terraform diff or a cloud verification."
    )
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    if cmd_plan(args):
        return 1
    cfg = load()
    if (
        not args.yes
        and input("Type the target project ID to deploy: ").strip() != cfg.project_id
    ):
        die("Deployment cancelled.")
    for step in deployment_steps(cfg):
        run(["mise", "run", step], capture=False)
    print(
        "Service checks passed. Complete the real-deck and signed-in Gemini Enterprise release gates in CLOUD_SHELL.md."
    )
    return 0


def cmd_ge_apps(args: argparse.Namespace) -> int:
    """List the caller's Gemini Enterprise apps, readably.

    agents-cli answers with one line of JSON. A tenancy with forty apps in it
    is then a wall of text an operator has to search by eye for the resource
    name they need to paste into deploy.env, so it is reformatted here.
    """
    cfg = load()
    proc = subprocess.run(
        [
            "uv",
            "run",
            "agents-cli",
            "publish",
            "gemini-enterprise",
            "--list",
            "--project",
            cfg.project_id,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    apps: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                apps = json.loads(line).get("apps", []) or []
            except json.JSONDecodeError:
                continue
    if not apps:
        print(proc.stdout or proc.stderr, end="")
        return proc.returncode

    apps.sort(key=lambda a: (a.get("location", ""), a.get("display_name", "")))
    width = max(len(a.get("display_name", "")) for a in apps)
    print(f"Gemini Enterprise apps in {cfg.project_id}:")
    print()
    for app in apps:
        name = app.get("display_name", "")
        print(
            f"  {name.ljust(width)}  {app.get('location', ''):<7} {app.get('name', '')}"
        )
    print()
    print("Copy the value in the last column into GE_APP_ID in deploy.env,")
    print("or leave it blank and `mise run register` will offer you this list.")
    return proc.returncode


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load()
    renderer = service_url(RENDERER_SERVICE, cfg)
    agent = service_url(AGENT_SERVICE, cfg)
    print(f"  project   {cfg.project_id} ({cfg.region})")
    print(f"  renderer  {renderer or 'not deployed'}")
    print(f"  agent     {agent or 'not deployed'}")
    if agent:
        print(f"  card      {agent_card_url(agent)}")
    print(f"  bucket    gs://{cfg.deck_bucket}")
    for entry in _existing_ge_agents(cfg):
        print(f"  GE agent  {entry.get('displayName', '?')}  {entry.get('name', '')}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Prove the deployment serves, rather than that it deployed.

    Reuses the same probe used against dev, so a green production result
    means the same five surfaces that are checked locally.
    """
    cfg = load()
    agent = require_service_url(AGENT_SERVICE, cfg)
    return subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/dev_local.py",
            "check",
            "--auth",
            "--base",
            agent,
        ],
        cwd=ROOT,
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("config", help="show the resolved configuration")
    p.add_argument("--json", action="store_true")
    p.add_argument("--shell", action="store_true", help="print KEY=value lines")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("configure", help="write deploy.env interactively")
    p.add_argument("--force", action="store_true", help="replace an existing file")
    p.set_defaults(func=cmd_configure)

    p = sub.add_parser("preflight", help="check the environment can deploy")
    p.set_defaults(func=cmd_preflight)
    p = sub.add_parser(
        "plan", help="show the selected deployment steps without cloud access"
    )
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser(
        "pin-renderer", help="record a built renderer digest for this target"
    )
    p.add_argument("--tag", required=True)
    p.set_defaults(func=cmd_renderer_release)
    p = sub.add_parser(
        "renderer-release", help="read the target-scoped immutable renderer image"
    )
    p.set_defaults(func=cmd_renderer_release)
    p = sub.add_parser("deploy", help="run the selected deployment after confirmation")
    p.add_argument(
        "--yes",
        action="store_true",
        help="explicit noninteractive approval for the displayed plan",
    )
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("agent-env", help="write .env from the live renderer URL")
    p.set_defaults(func=cmd_agent_env)

    p = sub.add_parser("grant-invoker", help="let Gemini Enterprise call the agent")
    p.set_defaults(func=cmd_grant_invoker)

    p = sub.add_parser("register", help="register the agent with Gemini Enterprise")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("ge-apps", help="list Gemini Enterprise apps in the project")
    p.set_defaults(func=cmd_ge_apps)

    p = sub.add_parser("status", help="show what is deployed")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("verify", help="smoke test the deployed agent")
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
