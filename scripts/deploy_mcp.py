"""Deploy the isolated MCP services; never edits an A2A registration."""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from datetime import UTC, datetime

from deploy_cli import (
    ROOT,
    load,
    parse_env_file,
    project_number,
    require_service_url,
    validate,
)
from deploy_templates import run, service

TERRAFORM = ROOT / "deployment/terraform/mcp"


def runtime_env(cfg, renderer, public_url):
    # Preserve explicit model overrides when adding MCP beside an existing A2A.
    current = (
        parse_env_file((ROOT / ".env").read_text()) if (ROOT / ".env").exists() else {}
    )
    models = {
        key: value
        for key, value in current.items()
        if key.startswith("SLIDEGEN_") and key.endswith("_MODEL")
    }
    if cfg.authoring_model:
        models["SLIDEGEN_AUTHORING_MODEL"] = cfg.authoring_model
    return models | {
        "GOOGLE_CLOUD_PROJECT": cfg.project_id,
        "GOOGLE_CLOUD_LOCATION": cfg.model_location,
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "MCP_DATABASE": "pixelpitch-mcp",
        "MCP_DRAFT_BUCKET": f"{cfg.project_id}-pixelpitch-mcp-drafts",
        "MCP_PUBLIC_URL": public_url,
        "MCP_OAUTH_CLIENT_ID": cfg.mcp_oauth_client_id,
        "MCP_ALLOWED_EMAIL_DOMAINS": cfg.mcp_allowed_email_domains,
        "SLIDEGEN_RENDERER_URL": renderer["status"]["url"],
        "SLIDEGEN_GCS_BUCKET": cfg.deck_bucket,
    }


def renderer_secret(renderer):
    for item in renderer["spec"]["template"]["spec"]["containers"][0].get("env", []):
        if item["name"] == "RENDERER_API_KEY":
            secret = item.get("valueFrom", {}).get("secretKeyRef", {}).get("name")
            if not secret:
                raise ValueError(
                    "Renderer key must use Secret Manager; do not copy a plaintext key into deployment files."
                )
            return secret
    return ""


def deploy(cfg):
    renderer = service(cfg, "slidegen-renderer")
    key = renderer_secret(renderer)
    number = project_number(cfg.project_id)
    public_url = f"https://pixelpitch-mcp-{number}.{cfg.region}.run.app"
    values = runtime_env(cfg, renderer, public_url)
    from app.mcp_cloud import CloudConfig

    CloudConfig(
        role="api",
        project=cfg.project_id,
        database=values["MCP_DATABASE"],
        bucket=values["MCP_DRAFT_BUCKET"],
        artifact_bucket=cfg.deck_bucket,
        renderer_url=values["SLIDEGEN_RENDERER_URL"],
        public_url=public_url,
        oauth_client_id=cfg.mcp_oauth_client_id,
        allowed_domains=frozenset(cfg.mcp_allowed_email_domains.split(",")),
    )
    run(["terraform", "init", "-input=false"], cwd=TERRAFORM)
    run(
        [
            "terraform",
            "apply",
            f"-var=project_id={cfg.project_id}",
            f"-var=region={cfg.region}",
            f"-var=deck_bucket={cfg.deck_bucket}",
            f"-var=renderer_secret={key}",
        ],
        cwd=TERRAFORM,
    )
    for suffix in ([], ["--check"]):
        run(["uv", "run", "python", "scripts/prepare_agy_skills.py", *suffix])
    run(
        ["uv", "run", "--extra", "mcp-app", "python", "scripts/check_mcp.py", "context"]
    )
    tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    image = f"{cfg.region}-docker.pkg.dev/{cfg.project_id}/{cfg.artifact_repo}/pixelpitch-mcp"
    run(
        [
            "gcloud",
            "builds",
            "submit",
            ".",
            "--project",
            cfg.project_id,
            "--config=mcp-app/cloudbuild.yaml",
            "--ignore-file=mcp-app/Dockerfile.dockerignore",
            f"--substitutions=_IMAGE={image}:{tag}",
        ]
    )
    digest = run(
        [
            "gcloud",
            "artifacts",
            "docker",
            "images",
            "describe",
            f"{image}:{tag}",
            "--project",
            cfg.project_id,
            "--format=value(image_summary.digest)",
        ],
        capture=True,
    )
    if not digest.startswith("sha256:"):
        raise RuntimeError("MCP image did not return an immutable digest")
    evidence = ROOT / ".tmp" / "deploy-mcp"
    evidence.mkdir(parents=True, exist_ok=True)
    for role in ("worker", "api"):
        name = "pixelpitch-mcp-worker" if role == "worker" else "pixelpitch-mcp"
        env_file = evidence / f"{role}.json"
        env_file.write_text(json.dumps(values | {"MCP_ROLE": role}))
        env_file.chmod(0o600)
        sizing = (
            [
                "--cpu=4",
                "--memory=8Gi",
                "--min-instances=1",
                "--max-instances=1",
                "--concurrency=1",
                "--no-cpu-throttling",
            ]
            if role == "worker"
            else [
                "--cpu=1",
                "--memory=2Gi",
                "--min-instances=1",
                "--max-instances=3",
                "--concurrency=40",
            ]
        )
        run(
            [
                "gcloud",
                "run",
                "deploy",
                name,
                "--project",
                cfg.project_id,
                "--region",
                cfg.region,
                f"--image={image}@{digest}",
                f"--service-account=pixelpitch-mcp-{role}@{cfg.project_id}.iam.gserviceaccount.com",
                "--no-allow-unauthenticated",
                "--timeout=60",
                *sizing,
                f"--env-vars-file={env_file}",
                *(
                    [f"--update-secrets=SLIDEGEN_RENDERER_API_KEY={key}:latest"]
                    if key
                    else []
                ),
                "--quiet",
            ]
        )
    if cfg.ge_app_id:
        ge_number = project_number(cfg.ge_app_id.split("/")[1])
        run(
            [
                "gcloud",
                "run",
                "services",
                "add-iam-policy-binding",
                "pixelpitch-mcp",
                "--project",
                cfg.project_id,
                "--region",
                cfg.region,
                f"--member=serviceAccount:service-{ge_number}@gcp-sa-discoveryengine.iam.gserviceaccount.com",
                "--role=roles/run.invoker",
                "--quiet",
            ]
        )
    else:
        print(
            "GE_APP_ID is empty: grant the intended GE service agent invoker access before connecting."
        )
    print(
        f"MCP endpoint: {public_url}/mcp\nComplete the customer OAuth connector steps in mcp-app/DEPLOY.md."
    )


def verify(cfg):
    import httpx

    url = require_service_url("pixelpitch-mcp", cfg)
    token = subprocess.check_output(
        ["gcloud", "auth", "print-identity-token"], text=True
    ).strip()
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        unauthenticated = client.get(url + "/mcp")
        if unauthenticated.status_code not in (401, 403):
            raise RuntimeError("Unauthenticated MCP access was not rejected")
        health = client.get(
            url + "/health", headers={"X-Serverless-Authorization": f"Bearer {token}"}
        )
        health.raise_for_status()
        no_user = client.post(
            url + "/mcp",
            headers={"X-Serverless-Authorization": f"Bearer {token}"},
            json={},
        )
        if no_user.status_code != 401:
            raise RuntimeError(
                "Could not prove app-level user-token enforcement (caller also needs run.invoker)"
            )
    print(
        "PASS: private API is healthy and rejects calls without a user token. Signed-in GE and real-deck checks remain required."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("deploy", "verify"))
    args = parser.parse_args()
    cfg = load()
    problems = validate(cfg)
    if cfg.deployment_mode not in ("mcp", "both"):
        problems.append("Set DEPLOYMENT_MODE=mcp or both before deploying MCP.")
    if problems:
        raise SystemExit("\n".join(problems))
    {"deploy": deploy, "verify": verify}[args.command](cfg)


if __name__ == "__main__":
    main()
