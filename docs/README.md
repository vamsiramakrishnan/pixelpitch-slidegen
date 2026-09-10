# Documentation

## Set up and use

- [Set up the MCP App](mcp-setup.md): Cloud Shell, OAuth, deployment, and a signed-in test.
- [Deploy from Cloud Shell](../CLOUD_SHELL.md): choose A2A, MCP, or both.
- [Prepare templates after upload](template-operations.md): install Eventarc, upload, check, and retry.

## Understand the system

- [Architecture](architecture.md): the service diagram and a plain-language explanation.
- [Template architecture](template-architecture.md): source versions, storage, retries, and fidelity limits.
- [A2A background jobs](a2a-background-jobs.md): the workstation experiment and its cloud boundary.

## Develop and operate

- [MCP development](../mcp-app/README.md): local widget and protocol tests.
- [MCP deployment reference](../mcp-app/DEPLOY.md): manual resource and IAM details.
- [Renderer](../renderer/README.md): conversion and export checks.
- [Workstation preview](workstation-preview.md): authenticated local experiments.
- [Standalone repository](standalone-repository.md): included components, exclusions, and source provenance.
- [Public release checklist](release-checklist.md): review the exact files and history before publishing.

## Check the documentation

From the repository root, run:

```bash
mise run docs-check
```

The check covers project-owned setup, architecture, and operations docs. It
checks local links, headings, shell syntax, referenced mise tasks, SVG structure,
and common forms of embedded cloud project identifiers. It also checks
`.env.example`, `deploy.env.example`, and `mise.toml` for those identifiers.
It does not contact Google Cloud, run deployment commands, or scan Git history
for secrets. Without mise, use `uv run python scripts/check_docs.py`.

Before publishing, also review the exact release snapshot for credentials,
customer data, generated output, private URLs, licenses, and copied assets.
Keep deployment evidence in private storage, not in public setup instructions.
