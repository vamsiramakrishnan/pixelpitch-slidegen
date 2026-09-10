"""Deploy the shared template-upload pipeline for the selected interfaces."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy_cli import load  # noqa: E402

from app.template_queue import enqueue  # noqa: E402
from app.template_versions import TemplateVersion, ready_prefix  # noqa: E402

TERRAFORM = ROOT / "deployment/terraform/template-preparation"
WORKER = "slidegen-template-worker"


def run(args, *, cwd=ROOT, capture=False):
    result = subprocess.run(
        args, cwd=cwd, check=True, text=True, capture_output=capture
    )
    return result.stdout.strip() if capture else ""


def service(cfg, name):
    return json.loads(
        run(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                name,
                "--project",
                cfg.project_id,
                "--region",
                cfg.region,
                "--format=json",
            ],
            capture=True,
        )
    )


def environment(service_value):
    return {
        item["name"]: item["value"]
        for item in service_value["spec"]["template"]["spec"]["containers"][0].get(
            "env", []
        )
        if "value" in item
    }


def configure_queue(cfg):
    values = environment(service(cfg, WORKER))
    for name in (
        "SLIDEGEN_TEMPLATE_QUEUE",
        "SLIDEGEN_TEMPLATE_WORKER_URL",
        "SLIDEGEN_TEMPLATE_TASK_SERVICE_ACCOUNT",
    ):
        os.environ[name] = values[name]
    os.environ["SLIDEGEN_GCS_BUCKET"] = cfg.deck_bucket
    os.environ["GOOGLE_CLOUD_PROJECT"] = cfg.project_id
    return values.get("SLIDEGEN_TEMPLATE_PREFIX", "templates/")


def current_sources(cfg, prefix):
    from google.cloud import storage

    client = storage.Client(project=cfg.project_id)
    for blob in client.list_blobs(cfg.deck_bucket, prefix=prefix):
        version = TemplateVersion(cfg.deck_bucket, blob.name, str(blob.generation))
        if version.is_source(cfg.deck_bucket, prefix):
            yield version


def backfill(cfg):
    prefix = configure_queue(cfg)
    from google.cloud import storage

    bucket = storage.Client(project=cfg.project_id).bucket(cfg.deck_bucket)
    for version in current_sources(cfg, prefix):
        if ready_prefix(bucket, version):
            print(
                json.dumps(
                    {
                        "source": version.uri,
                        "generation": version.generation,
                        "state": "ready",
                    }
                ),
                flush=True,
            )
        else:
            print(
                json.dumps(
                    {
                        "source": version.uri,
                        "generation": version.generation,
                        "task": enqueue(version),
                    }
                ),
                flush=True,
            )


def status(cfg):
    from google.api_core.exceptions import NotFound
    from google.cloud import storage

    prefix = configure_queue(cfg)
    bucket = storage.Client(project=cfg.project_id).bucket(cfg.deck_bucket)
    for version in current_sources(cfg, prefix):
        committed = ready_prefix(bucket, version)
        try:
            job = json.loads(bucket.blob(version.status_path).download_as_bytes())
        except NotFound:
            job = {"state": "not_started_or_queued"}
        print(
            json.dumps(
                {
                    "source": version.uri,
                    "generation": version.generation,
                    **job,
                    "ready": bool(committed),
                }
            ),
            flush=True,
        )


def deploy(cfg):
    renderer = service(cfg, "slidegen-renderer")
    clients = (["slidegen-agent"] if cfg.deployment_mode in ("a2a", "both") else []) + (
        ["pixelpitch-mcp", "pixelpitch-mcp-worker"]
        if cfg.deployment_mode in ("mcp", "both")
        else []
    )
    services = {name: service(cfg, name) for name in clients}
    agent = services[clients[0]]
    revision = renderer["status"]["latestReadyRevisionName"]
    renderer_image = run(
        [
            "gcloud",
            "run",
            "revisions",
            "describe",
            revision,
            "--project",
            cfg.project_id,
            "--region",
            cfg.region,
            "--format=value(status.imageDigest)",
        ],
        capture=True,
    )
    if "@sha256:" not in renderer_image:
        raise RuntimeError("renderer revision has no pinned image digest")
    for suffix in ([], ["--check"]):
        run(["uv", "run", "python", "scripts/prepare_agy_skills.py", *suffix])
    tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    image = f"{cfg.region}-docker.pkg.dev/{cfg.project_id}/{cfg.artifact_repo}/{WORKER}"
    run(
        [
            "gcloud",
            "builds",
            "submit",
            ".",
            "--project",
            cfg.project_id,
            "--config",
            "template-worker/cloudbuild.yaml",
            "--ignore-file",
            "template-worker/gcloudignore",
            "--substitutions",
            f"_TAG={tag},_REGION={cfg.region},_REPO={cfg.artifact_repo},_RENDERER_IMAGE={renderer_image}",
        ],
        cwd=ROOT,
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
        raise RuntimeError("preparation image has no digest")
    renderer_env = renderer["spec"]["template"]["spec"]["containers"][0].get("env", [])
    key = next(
        (
            item.get("valueFrom", {}).get("secretKeyRef", {}).get("name", "")
            for item in renderer_env
            if item["name"] == "RENDERER_API_KEY"
        ),
        "",
    )
    agent_env = environment(agent)
    models = {
        name: value
        for name, value in agent_env.items()
        if name.endswith("_MODEL") or name == "GOOGLE_CLOUD_LOCATION"
    }
    models.setdefault("GOOGLE_CLOUD_LOCATION", cfg.model_location)
    if cfg.authoring_model:
        models["SLIDEGEN_AUTHORING_MODEL"] = cfg.authoring_model
    variables = {
        "project_id": cfg.project_id,
        "region": cfg.region,
        "deck_bucket": cfg.deck_bucket,
        "worker_image": f"{image}@{digest}",
        "renderer_url": renderer["status"]["url"],
        "agent_service_account": agent["spec"]["template"]["spec"][
            "serviceAccountName"
        ],
        "additional_service_accounts": {
            name: value["spec"]["template"]["spec"]["serviceAccountName"]
            for name, value in services.items()
            if name != clients[0]
        },
        "renderer_secret": key,
        "model_environment": models,
        "template_prefix": environment(renderer).get(
            "SLIDEGEN_TEMPLATE_PREFIX", "templates/"
        ),
    }
    run(["terraform", "init", "-input=false"], cwd=TERRAFORM)
    plan = ROOT / ".tmp" / f"template-preparation-{tag}.tfplan"
    plan.parent.mkdir(exist_ok=True)
    args = [
        f"-var={name}={json.dumps(value) if isinstance(value, dict) else value}"
        for name, value in variables.items()
    ]
    run(["terraform", "plan", "-input=false", f"-out={plan}", *args], cwd=TERRAFORM)
    run(["terraform", "apply", "-input=false", str(plan)], cwd=TERRAFORM)
    configure_queue(cfg)
    env = ",".join(
        f"{name}={os.environ[name]}"
        for name in (
            "SLIDEGEN_TEMPLATE_QUEUE",
            "SLIDEGEN_TEMPLATE_WORKER_URL",
            "SLIDEGEN_TEMPLATE_TASK_SERVICE_ACCOUNT",
        )
    )
    for name in clients:
        run(
            [
                "gcloud",
                "run",
                "services",
                "update",
                name,
                "--project",
                cfg.project_id,
                "--region",
                cfg.region,
                "--update-env-vars",
                env,
                "--quiet",
            ]
        )
    backfill(cfg)
    print(
        "Template trigger deployed; backfill enqueued. Run mise run templates-status to check readiness."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("deploy", "backfill", "status"):
        sub.add_parser(command)
    retry = sub.add_parser("retry")
    retry.add_argument("--gs-uri", required=True)
    retry.add_argument(
        "--retry-id",
        required=True,
        help="Unique attempt label for a terminally failed generation",
    )
    args = parser.parse_args()
    cfg = load()
    if args.command == "retry":
        from google.cloud import storage

        prefix = configure_queue(cfg)
        bucket, _, name = args.gs_uri.removeprefix("gs://").partition("/")
        blob = storage.Client(project=cfg.project_id).bucket(bucket).blob(name)
        blob.reload()
        version = TemplateVersion.from_uri(args.gs_uri, str(blob.generation))
        if not version.is_source(cfg.deck_bucket, prefix):
            raise ValueError("source is not a configured template upload")
        print(enqueue(version, retry_id=args.retry_id))
    else:
        {"deploy": deploy, "backfill": backfill, "status": status}[args.command](cfg)


if __name__ == "__main__":
    main()
