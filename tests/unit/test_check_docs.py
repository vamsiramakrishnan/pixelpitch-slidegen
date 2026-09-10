"""Public documentation examples must stay portable and safe to inspect."""

from pathlib import Path

from scripts.check_docs import (
    anchors,
    check_document,
    check_private_values,
    check_svg,
    documents,
    public_configurations,
)


def test_accepts_configured_cloud_commands(tmp_path: Path):
    text = '# Setup\n\n```bash\nmise run deploy-mcp\ngcloud run services list --project "$PROJECT_ID"\n```\n'
    assert check_document(tmp_path / "README.md", text, {"deploy-mcp"}, tmp_path) == []


def test_detects_private_values_without_echoing_them(tmp_path: Path):
    text = "# Setup\n\nprojects/123456789012/locations/global\n"
    errors = check_document(tmp_path / "README.md", text, set(), tmp_path)
    assert any("literal cloud project number" in error for error in errors)
    assert all("123456789012" not in error for error in errors)


def test_checks_links_and_mise_tasks(tmp_path: Path):
    (tmp_path / "guide.md").write_text("# Guide\n\n## Run it\n")
    text = "# Setup\n\n[valid](guide.md#run-it) [bad](guide.md#absent)\n\nmise run missing\n"
    errors = check_document(tmp_path / "README.md", text, set(), tmp_path)
    assert len(errors) == 2
    assert any("missing heading anchor" in error for error in errors)
    assert any("unknown mise task" in error for error in errors)


def test_checks_shell_without_executing_it(tmp_path: Path):
    output = tmp_path / "must-not-exist"
    text = f"# Setup\n\n```bash\ntouch '{output}'\n```\n"
    assert check_document(tmp_path / "README.md", text, set(), tmp_path) == []
    assert not output.exists()
    malformed = '# Setup\n\n```bash\necho "unterminated\n```\n'
    assert any(
        "invalid shell syntax" in error
        for error in check_document(tmp_path / "README.md", malformed, set(), tmp_path)
    )


def test_fenced_headings_are_not_anchors():
    assert anchors(
        "# Guide\n\n```bash\n# Not a heading\n```\n\n## Run it\n\n## Run it\n"
    ) == {"guide", "run-it", "run-it-1"}


def test_svg_requires_accessibility_and_no_scripts(tmp_path: Path):
    path = tmp_path / "diagram.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    )
    errors = check_svg(path)
    assert any("title" in error for error in errors)
    assert any("scripts" in error for error in errors)


def test_public_configuration_is_included(tmp_path: Path):
    assert {path.name for path in public_configurations(tmp_path)} == {
        ".env.example",
        "deploy.env.example",
        "mise.toml",
    }
    assert check_private_values('MCP_URL="https://service-123456789012.region.run.app"')
    assert not check_private_values(
        'MCP_URL="https://pixelpitch-mcp-${PROJECT_NUMBER}.${REGION}.run.app"'
    )


def test_third_party_notice_links_are_checked(tmp_path: Path):
    assert tmp_path / "THIRD_PARTY_NOTICES.md" in documents(tmp_path)
