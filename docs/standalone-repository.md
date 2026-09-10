# Standalone repository and source provenance

This repository contains the slide-generation services extracted from
[Pixelpitch](https://github.com/vamsiramakrishnan/pixelpitch). The converter and
required slide skills are local. Builds and setup do not need a parent checkout.

## Included components

| Path | Content |
|---|---|
| `app/` | Shared ADK pipeline, A2A, MCP API, job state, and template preparation |
| `agy-worker/` | Separately locked Antigravity runtime and four slide skills |
| `renderer/` | PowerPoint conversion service and export checks |
| `vendor/slidify/` | Converter package, assets, and regression tests |
| `template-worker/` | Upload-triggered preparation image |
| `mcp-app/` | Widget, local host, and separate UI dependency lockfile |
| `deployment/`, `scripts/`, `docs/` | Infrastructure, setup tasks, checks, and guides |
| `workstation-relay/` | Optional local experiment, not the customer deployment route |

ADK and Antigravity keep separate Python environments because their Protobuf
versions conflict. Moving the repository does not change the native A2A agent,
Gemini Enterprise composite A2UI catalog, or MCP's separate OAuth connection.

## Four skills, loaded when needed

Antigravity discovers only the skills under `agy-worker/skills` by default:

- `pixelpitch-deck` coordinates deck authoring and validation.
- `pixelpitch-slide-craft` guides slide composition and visual quality.
- `pixelpitch-brand-derivation` derives the brand contract.
- `pixelpitch-template-roundtrip` parses templates and preserves source layouts.

The broad repository skill catalog is excluded. The worker does not discover
skills or grant workspace access by searching parent directories. Optional
`SLIDEGEN_AGY_SKILLS_PATHS` and `SLIDEGEN_AGY_WORKSPACES` values explicitly add
customer-approved directories.

Brand files and the compact style index at `app/assets/styles/index.json`
remain available. Selecting a style does not require the original skill catalog.
The four bundled skills retain their supporting references and scripts.

## Excluded files

The repository excludes the Pixelpitch desktop, web application, daemon,
JavaScript workspace, and unrelated demos. It also excludes installed
dependencies, virtual environments, runtime databases, generated decks,
evaluation traces, customer templates, credentials, and cloud state.

Keep `.env`, `deploy.env`, Terraform state, and test output private. Public
examples use placeholders and read the deployment project from configuration.
The [release checklist](release-checklist.md) covers future publications.

## Source provenance and history

The initial release uses a fresh Git history instead of importing unrelated
Pixelpitch commits. [The source manifest](provenance/source-manifest.json)
records the source repository, base commit, copied paths, and SHA-256 hashes.

Those hashes describe the source working-tree files before standalone path
adjustments. They are not hashes of the final release files. The published Git
commit identifies the final snapshot. Applicable licenses and attribution are
retained in [LICENSE](../LICENSE) and [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Deployment remains a separate action

Publishing source code does not redeploy Cloud Run, change an existing Gemini
Enterprise registration, or enable a template upload trigger. Use
[Cloud Shell deployment](../CLOUD_SHELL.md) for your chosen project.

Local tests and container builds do not prove a customer's signed-in widget,
model access, or template quality. Before releasing a deployment, run the real
download test and the [template overwrite test](template-operations.md#6-verify-the-upload-to-deck-path).
