# Pixelpitch Slidegen

[Apache License 2.0](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

Create editable PowerPoint decks in Gemini Enterprise. Use an interactive MCP
App, an A2A agent with A2UI cards, or both. Both interfaces share slide authoring,
template preparation, and the PowerPoint renderer.

## Start here

| I want to… | Read |
|---|---|
| Try the widget without cloud credentials | [Local preview](#develop-and-verify) |
| Set up the MCP App in my project | [MCP setup, step by step](docs/mcp-setup.md) |
| Deploy A2A, MCP, or both from Cloud Shell | [Cloud Shell deployment](CLOUD_SHELL.md) |
| Understand the services and data flow | [Architecture](docs/architecture.md) |
| Parse templates automatically when files change | [Template upload and recovery](docs/template-operations.md) |
| Change the widget and test the user experience | [Developer loop](docs/development.md) |
| Check template fidelity and export behavior | [Template architecture](docs/template-architecture.md) and [renderer](renderer/README.md) |
| Understand licensing and attribution | [License](#license) |

All setup values come from your configuration. No project ID, tenant, OAuth
client, or customer template is provided by this repository.

![MCP deck flow and the separate upload-triggered template preparation flow](docs/assets/architecture.svg)

The [architecture explanation](docs/architecture.md) describes the diagram in text.

## Choose an interface

| Interface | What the user sees | How generation runs |
|---|---|---|
| MCP App | Interactive brief, draft previews, progress, cancellation, and download | A saved job continues in a separate worker |
| A2A with A2UI | Native Gemini Enterprise intake and result cards, with streamed progress | The Cloud Run agent generates within the request |

MCP's available final previews come from the exported PowerPoint. The downloaded
`.pptx`, not the preview image, is the editable file.

A2A uses the native ADK agent and Gemini Enterprise's composite A2UI catalog.
Saved-job A2A behavior currently belongs to the workstation experiment, not
Cloud Run.

MCP has its own OAuth connector. Adding MCP does not require replacing an A2A
registration. Local browser tests do not prove that a particular Gemini
Enterprise tenant supports the widget. Complete the signed-in setup test.

## Prepare a checkout

This repository contains the agent, renderer, and Slidify converter. It does
not need the Pixelpitch desktop or web application, a parent checkout, or its
JavaScript workspace. See [what is included](docs/standalone-repository.md).

Install [mise](https://mise.jdx.dev/getting-started.html), review `mise.toml`, then run:

```bash
git clone https://github.com/vamsiramakrishnan/pixelpitch-slidegen.git
cd pixelpitch-slidegen
mise trust
mise install
mise run setup
```

`setup` installs the locked Python environments and checks the four slide skills.
It does not create cloud resources. ADK and Antigravity have separate Python
environments because their dependency versions conflict. Keep that separation.

Antigravity loads only deck orchestration, slide craft, brand derivation, and
template round-tripping skills. The broader skill catalog is not included.
Brand and style choices remain available as local data, without loading their
source skill documents into the worker's context.

Continue with the [MCP guide](docs/mcp-setup.md) or [Cloud Shell guide](CLOUD_SHELL.md).
Do not run a full deployment against existing resources that this checkout's
Terraform state does not own.

## Connect Gemini Enterprise

Deployment creates billable resources and changes IAM. The MCP deployment also
keeps a worker running continuously. Use the [Cloud Shell guide](CLOUD_SHELL.md)
for prerequisites, authentication, resource review, and the deployment commands.

After preparing the checkout and authenticating as described in that guide:

```bash
mise run configure
mise run plan
```

`configure` asks for your project, region, existing Gemini Enterprise app,
interface choice, and template preparation mode. It saves your choices in the
ignored `deploy.env`. `plan` shows the steps without contacting Google Cloud.
Neither command deploys anything.

Choose `a2a`, `mcp`, or `both` for `DEPLOYMENT_MODE`:

- A2A deployment uses `agents-cli` to publish the agent card. It reuses a
  matching existing registration. It stops on an invalid registration ID or
  a failed registration lookup instead of creating a duplicate.
- MCP deployment creates the private API and worker, then prints the endpoint.
  You must still [create the OAuth connector, enable actions, and sign in](docs/mcp-setup.md).
  The script does not create the connector for you. Put the OAuth client secret
  only in the Gemini Enterprise connector, never in the widget or Git.
- Both interfaces share the renderer and prepared templates. MCP does not
  replace an A2A registration.

Finish with a signed-in conversation in your target Gemini Enterprise app.
Generate a deck, download the `.pptx`, and open it to check content and
editability. A healthy service or a working local preview is not that test.

## Prepare templates when files change

Choose `automatic` for `TEMPLATE_PREPARATION` during setup to deploy the upload
trigger. The same `gs://YOUR_BUCKET/templates/company.pptx` URI can be reused:

1. Upload or replace the `.pptx` directly under `templates/` in your configured
   bucket. Cloud Storage assigns the uploaded content a new generation number.
2. Eventarc tells the private template worker that the upload is complete.
   Eventarc sends the notification; it does not parse PowerPoint files.
3. The worker queues a Cloud Tasks job. That job extracts every source slide,
   prepares the template, and validates the result.
4. The worker publishes a ready bundle for that exact generation. Both A2A and
   MCP read that bundle when using the template.

Run `mise run templates-status` and wait for `ready: true` for the current
generation. An old bundle is not silently used after the source changes.
Existing generated decks are not changed by a template upload.

Changing only object metadata does not trigger parsing. Nested files such as
`templates/team/company.pptx` are not accepted by this upload pipeline.
Use `mise run templates-backfill` for existing source files without ready
bundles. For upload commands and failure recovery, see
[template operations](docs/template-operations.md). If you choose `on-demand`
instead, preparation starts when a template is first used, not at upload time.

Template-based output preserves measured layout positions and source artwork.
It does not copy native PowerPoint masters or guarantee pixel-identical output.
The renderer checks for missing authored text and editability before publishing.

## Develop and verify

For a first local preview without cloud credentials, run:

```bash
mise run setup-dev
mise run demo
```

On your own machine, open `http://127.0.0.1:18092`. On Cloud Workstations, open
the HTTPS preview URL printed by the command. This runs the real MCP widget,
job queue, draft previews, and cancellation with synthetic slides and placeholder
downloads. It does not generate a real PowerPoint or prove Gemini Enterprise
compatibility. The host labels the mode and lets you test narrow chat columns.

Enter a six-slide brief and select **Generate deck**. Check that drafts appear,
then try cancellation, reconnect, and the host's **Chat width** and **Theme**
controls. Press Ctrl+C to stop the demo. These controls help test the widget;
they are not additions to Gemini Enterprise.

Run `mise run doctor` for local dependency checks and repair commands. After
changing the UI, run `mise run build-ui` and reload the browser. For real model
calls and export, configure `.env` and use `mise run dev` on port 18093.
See the [developer loop](docs/development.md) for the progression to a signed-in test.

Run these verification commands from this directory:

```bash
uv run pytest tests/unit tests/integration
mise run docs-check
```

After `mise run setup-dev`, run the MCP build and browser checks:

```bash
mise run test-mcp
```

For workstation authentication, iframe extension errors, and Linux browser
dependencies, see [local troubleshooting](docs/development.md#troubleshoot-the-preview).

For local A2A experiments, use the [workstation guide](docs/workstation-preview.md).
Do not use a workstation relay as a customer production deployment.

## Repository map

| Directory | Owns |
|---|---|
| `app/` | ADK agent, A2A/A2UI, MCP API, job state, and shared template logic |
| `agy-worker/` | Isolated Antigravity authoring and four slide-generation skills |
| `mcp-app/` | Interactive widget, local host, and MCP image build |
| `renderer/` | Slidify conversion, source extraction, and export checks |
| `vendor/slidify/` | Local converter package, assets, and regression tests |
| `template-worker/` | Preparation image built from the renderer image |
| `deployment/terraform/` | Shared, MCP, and template infrastructure modules |
| `scripts/` | Setup, deployment, diagnostics, and verification |
| `docs/` | Setup guides, architecture, and operations |

## Before a public release

Follow the [public release checklist](docs/release-checklist.md). It covers the
documentation check, tracked generated files, credentials, history, licenses,
and clean-checkout verification. Passing the documentation check alone does
not make the full repository ready to publish.

## License

Except for the third-party material identified in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), this repository's source code
and documentation are licensed under the [Apache License, Version 2.0](LICENSE).
You can use, modify, and redistribute them, including commercially, subject to
the license's terms. The software is provided without warranties.

Keep existing copyright and attribution notices when redistributing this
project. Vendored code, installed dependencies, and adapted design material
retain their own applicable licenses. Their bundled notices and license copies
are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [licenses/](licenses/).

The repository license does not grant rights to customer templates, logos,
proprietary fonts, or other supplied assets. Use assets you have permission to
use. No customer templates or credentials are bundled with the project.
