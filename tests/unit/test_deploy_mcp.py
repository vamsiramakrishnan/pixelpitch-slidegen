import importlib
from pathlib import Path

import pytest


@pytest.fixture
def deploy_mcp(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("deploy_mcp")


def renderer(env=()):
    return {
        "status": {"url": "https://renderer.run.app"},
        "spec": {"template": {"spec": {"containers": [{"env": list(env)}]}}},
    }


def test_renderer_auth_supports_iam_only_or_a_secret_reference(deploy_mcp):
    assert deploy_mcp.renderer_secret(renderer()) == ""
    assert (
        deploy_mcp.renderer_secret(
            renderer(
                [
                    {
                        "name": "RENDERER_API_KEY",
                        "valueFrom": {
                            "secretKeyRef": {"name": "customer-renderer-key"}
                        },
                    }
                ]
            )
        )
        == "customer-renderer-key"
    )
    with pytest.raises(ValueError, match="Secret Manager"):
        deploy_mcp.renderer_secret(
            renderer([{"name": "RENDERER_API_KEY", "value": "plaintext"}])
        )


def test_worker_model_and_customer_domains_are_preserved(
    deploy_mcp, tmp_path, monkeypatch
):
    from deploy_cli import Config

    monkeypatch.setattr(deploy_mcp, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "SLIDEGEN_AUTHORING_MODEL=current-model\nPRIVATE_KEY=not-for-upload\n"
    )
    cfg = Config(
        project_id="customer-decks",
        deployment_mode="mcp",
        mcp_oauth_client_id="123-client.apps.googleusercontent.com",
        mcp_allowed_email_domains="example.com,example.org",
    )
    values = deploy_mcp.runtime_env(cfg, renderer(), "https://mcp.run.app")
    assert values["SLIDEGEN_AUTHORING_MODEL"] == "current-model"
    assert values["MCP_ALLOWED_EMAIL_DOMAINS"] == "example.com,example.org"
    assert values["MCP_DRAFT_BUCKET"] != values["SLIDEGEN_GCS_BUCKET"]
    assert "PRIVATE_KEY" not in values


def test_deployment_uses_private_services_and_mounts_renderer_key(
    deploy_mcp, tmp_path, monkeypatch
):
    from deploy_cli import Config

    calls = []
    monkeypatch.setattr(deploy_mcp, "ROOT", tmp_path)
    monkeypatch.setattr(deploy_mcp, "project_number", lambda project: "123456")
    monkeypatch.setattr(
        deploy_mcp,
        "service",
        lambda *args: renderer(
            [
                {
                    "name": "RENDERER_API_KEY",
                    "valueFrom": {"secretKeyRef": {"name": "renderer-key"}},
                }
            ]
        ),
    )

    def run(args, **kwargs):
        calls.append(args)
        return "sha256:" + "a" * 64 if kwargs.get("capture") else ""

    monkeypatch.setattr(deploy_mcp, "run", run)
    cfg = Config(
        project_id="customer-decks",
        deployment_mode="mcp",
        mcp_oauth_client_id="123-client.apps.googleusercontent.com",
        mcp_allowed_email_domains="example.com",
        ge_app_id="projects/999/locations/global/collections/default_collection/engines/retail",
    )
    deploy_mcp.deploy(cfg)
    deployments = [args for args in calls if args[:3] == ["gcloud", "run", "deploy"]]
    assert [args[3] for args in deployments] == [
        "pixelpitch-mcp-worker",
        "pixelpitch-mcp",
    ]
    assert "--no-cpu-throttling" in deployments[0]
    for args in deployments:
        assert "--no-allow-unauthenticated" in args
        assert "--update-secrets=SLIDEGEN_RENDERER_API_KEY=renderer-key:latest" in args
        assert any("@sha256:" in arg for arg in args)
    assert not any("slidegen-agent" in args for args in calls)
