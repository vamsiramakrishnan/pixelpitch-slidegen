"""Tests for the deployment configuration lever.

The shell-out paths are not tested here; they are thin wrappers over gcloud
and are proven by running a deploy. What is tested is the part that decides
what a deploy will do, because a wrong value here fails twenty minutes later
against someone else's production project.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "deploy_cli",
    Path(__file__).resolve().parents[2] / "scripts" / "deploy_cli.py",
)
assert _SPEC and _SPEC.loader
deploy_cli = importlib.util.module_from_spec(_SPEC)
# Registered before exec because @dataclass resolves annotations through
# sys.modules, and a module loaded by path is not there yet.
sys.modules["deploy_cli"] = deploy_cli
_SPEC.loader.exec_module(deploy_cli)

Config = deploy_cli.Config


@pytest.mark.parametrize("mode", ["a2a", "mcp", "both"])
def test_selected_interfaces_control_deploy_steps(mode):
    cfg = Config(project_id="customer-decks", deployment_mode=mode)
    steps = deploy_cli.deployment_steps(cfg)
    assert ("deploy-agent" in steps) == (mode in ("a2a", "both"))
    assert ("register" in steps) == (mode in ("a2a", "both"))
    assert ("deploy-mcp" in steps) == (mode in ("mcp", "both"))
    assert steps.index("deploy-renderer") < steps.index("deploy-templates")
    assert "deploy-templates" not in deploy_cli.deployment_steps(
        Config(project_id="customer-decks", template_preparation="on-demand")
    )


def test_mcp_config_requires_explicit_user_identity():
    assert (
        len(
            deploy_cli.validate(
                Config(project_id="customer-decks", deployment_mode="mcp")
            )
        )
        == 2
    )
    assert (
        deploy_cli.validate(
            Config(
                project_id="customer-decks",
                deployment_mode="both",
                mcp_oauth_client_id="123-abc.apps.googleusercontent.com",
                mcp_allowed_email_domains="example.com,example.org",
            )
        )
        == []
    )


def test_shell_config_values_are_quoted(monkeypatch, capsys):
    import argparse
    import shlex

    cfg = Config(project_id="customer-decks")
    monkeypatch.setattr(deploy_cli, "load", lambda: cfg)
    assert deploy_cli.cmd_config(argparse.Namespace(json=False, shell=True)) == 0
    values = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert shlex.split(values["AUTHORING_MODEL"]) == [""]
    assert values["DEPLOYMENT_MODE"] == "a2a"


def test_model_and_unrelated_env_are_preserved(monkeypatch, tmp_path):
    import argparse

    target = tmp_path / ".env"
    original = (
        'SLIDEGEN_AUTHORING_MODEL=existing-model\nCUSTOM_SETTING="value # retained"\n'
    )
    target.write_text(original)
    monkeypatch.setattr(deploy_cli, "ROOT", tmp_path)
    monkeypatch.setattr(deploy_cli, "load", lambda: Config(project_id="customer-decks"))
    monkeypatch.setattr(
        deploy_cli, "require_service_url", lambda *args: "https://renderer.run.app"
    )
    deploy_cli.cmd_agent_env(argparse.Namespace())
    deploy_cli.cmd_agent_env(argparse.Namespace())
    assert original in target.read_text()
    assert len(list(tmp_path.glob(".env.before-deploy-*"))) == 2
    assert target.stat().st_mode & 0o777 == 0o600


def test_inline_registration_is_reused_without_publish(monkeypatch):
    import argparse
    import json

    cfg = Config(project_id="customer-decks", ge_agent_id="old-id")
    monkeypatch.setattr(deploy_cli, "load", lambda: cfg)
    monkeypatch.setattr(
        deploy_cli,
        "require_service_url",
        lambda *args: "https://slidegen-agent-hash.run.app",
    )
    monkeypatch.setattr(deploy_cli, "project_number", lambda *args: "123456")
    monkeypatch.setattr(
        deploy_cli,
        "_existing_ge_agents",
        lambda cfg: [
            {
                "name": "agents/old-id",
                "a2aAgentDefinition": {
                    "jsonAgentCard": json.dumps(
                        {
                            "url": "https://slidegen-agent-123456.us-central1.run.app/a2a/app"
                        }
                    )
                },
            }
        ],
    )
    monkeypatch.setattr(
        deploy_cli.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("must not publish a duplicate"),
    )
    assert deploy_cli.cmd_register(argparse.Namespace()) == 0


def test_registration_listing_failure_blocks_creation(monkeypatch):
    import urllib.error

    cfg = Config(
        project_id="customer-decks",
        ge_app_id="projects/123/locations/global/collections/default_collection/engines/retail",
    )
    monkeypatch.setattr(deploy_cli, "run", lambda *args: "token")

    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("access denied")

    monkeypatch.setattr(deploy_cli.urllib.request, "urlopen", unavailable)
    with pytest.raises(SystemExit):
        deploy_cli._existing_ge_agents(cfg)


def test_explicit_model_is_written_only_when_selected():
    cfg = Config(project_id="customer-decks", authoring_model="customer-model")
    assert "SLIDEGEN_AUTHORING_MODEL=customer-model" in deploy_cli.render_agent_env(
        cfg, "https://renderer.run.app"
    )
    assert "SLIDEGEN_AUTHORING_MODEL" not in deploy_cli.render_agent_env(
        Config(project_id="customer-decks"), "https://renderer.run.app"
    )


def test_bucket_defaults_to_something_globally_unique():
    """Bucket names are a global namespace, so a fixed default collides
    with the first other person who deploys this."""
    assert Config(project_id="example-retail-decks").deck_bucket == (
        "example-retail-decks-slidegen-decks"
    )


def test_a_bucket_name_with_a_slash_is_rejected():
    """The defect that made the previous Terraform unappliable.

    It interpolated a project *name* of "agents/pixelpitch-slidegen" into a
    bucket name. Nothing checked it, so the error surfaced as a Terraform
    apply failure rather than as a bad config value.
    """
    problems = deploy_cli.validate(
        Config(project_id="example-retail-decks", deck_bucket="agents/pixelpitch-decks")
    )

    assert any("DECK_BUCKET" in p for p in problems)


@pytest.mark.parametrize(
    "project_id",
    ["Example Retail", "ww", "example-retail_decks", "1example-retail", "example-retail-"],
)
def test_invalid_project_ids_are_named_before_anything_is_built(project_id: str):
    problems = deploy_cli.validate(Config(project_id=project_id))

    assert any("PROJECT_ID" in p for p in problems)


def test_every_problem_is_reported_not_just_the_first():
    """An operator in Cloud Shell should learn about all of it in one pass."""
    problems = deploy_cli.validate(
        Config(
            project_id="BAD",
            region="",
            deck_bucket="Not/Valid",
            ge_app_id="my-app",
        )
    )

    assert len(problems) == 4


def test_a_valid_config_has_nothing_to_say():
    assert (
        deploy_cli.validate(
            Config(
                project_id="example-retail-decks",
                ge_app_id=(
                    "projects/123/locations/global/collections/"
                    "default_collection/engines/retail_1751440313229"
                ),
            )
        )
        == []
    )


def test_the_agent_card_url_is_the_a2a_path_not_the_service_root():
    """Registering the bare service URL yields an agent that resolves and
    renders nothing, which is indistinguishable from a broken agent."""
    assert deploy_cli.agent_card_url("https://slidegen-agent-123.run.app/") == (
        "https://slidegen-agent-123.run.app/a2a/app/.well-known/agent-card.json"
    )


def test_the_gemini_enterprise_principal_uses_the_project_number():
    """Project id here instead of number is the commonest hand-typed error,
    and it fails as a silent 403 at deck time rather than at grant time."""
    assert deploy_cli.discovery_engine_agent("123456789012") == (
        "service-123456789012@gcp-sa-discoveryengine.iam.gserviceaccount.com"
    )


def test_the_image_path_follows_the_configured_region_and_repo():
    cfg = Config(
        project_id="example-retail-decks", region="australia-southeast1", artifact_repo="wx"
    )

    assert cfg.image == (
        "australia-southeast1-docker.pkg.dev/example-retail-decks/wx/slidegen-renderer"
    )


def test_config_file_parsing_ignores_comments_quotes_and_exports():
    parsed = deploy_cli.parse_env_file(
        "\n".join(
            [
                "# a comment",
                "",
                "PROJECT_ID=example-retail-decks",
                'REGION="australia-southeast1"',
                "export GE_APP_ID='projects/1/locations/global'",
                "NOT_A_PAIR",
            ]
        )
    )

    assert parsed == {
        "PROJECT_ID": "example-retail-decks",
        "REGION": "australia-southeast1",
        "GE_APP_ID": "projects/1/locations/global",
    }


def test_the_environment_overrides_the_config_file(tmp_path: Path):
    """So CI and a one-off `PROJECT_ID=x mise run deploy` work without an edit."""
    path = tmp_path / "deploy.env"
    path.write_text("PROJECT_ID=from-file\nREGION=us-central1\n", encoding="utf-8")

    cfg = deploy_cli.load(path, {"PROJECT_ID": "from-env"})

    assert cfg.project_id == "from-env"
    assert cfg.region == "us-central1"


def test_a_missing_project_id_stops_with_a_next_step(tmp_path: Path):
    with pytest.raises(SystemExit):
        deploy_cli.load(tmp_path / "absent.env", {})


def test_the_generated_agent_env_carries_the_live_renderer_url():
    """The value this file exists for. A stale renderer URL deploys clean
    and 404s on every deck request."""
    rendered = deploy_cli.render_agent_env(
        Config(project_id="example-retail-decks"),
        "https://slidegen-renderer-999.run.app",
    )
    values = deploy_cli.parse_env_file(rendered)

    assert values["SLIDEGEN_RENDERER_URL"] == "https://slidegen-renderer-999.run.app"
    assert values["GOOGLE_CLOUD_PROJECT"] == "example-retail-decks"
    assert values["SLIDEGEN_GCS_BUCKET"] == "example-retail-decks-slidegen-decks"
    assert values["GOOGLE_GENAI_USE_VERTEXAI"] == "true"


def test_the_generated_agent_env_says_it_is_generated():
    """It sits next to a hand-edited deploy.env, so it has to be obvious
    which one an operator is allowed to change."""
    rendered = deploy_cli.render_agent_env(Config(project_id="example-retail-decks"), "u")

    assert rendered.splitlines()[0].startswith("#")
    assert "deploy.env" in rendered
