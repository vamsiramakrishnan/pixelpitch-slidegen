# Develop the Gemini Enterprise experience

Use this standalone repository as the only source of truth. Do not copy
changes back from the retired Pixelpitch monorepo.

## Start with a local preview

```bash
mise trust
mise install
mise run setup-dev
mise run demo
```

Open `http://127.0.0.1:18092`. Enter a six-slide brief, choose a brand, and
select **Generate deck**. Watch drafts arrive before the job finishes. Try
**Reconnect app**, narrow the chat column, change the theme, and cancel a run.
Start a topic with `Fail` to exercise error recovery.

This uses the production widget, MCP bridge, SQLite jobs, and worker loop.
Only authoring and rendering are synthetic. The host labels this clearly.
The download URL is a placeholder, not a real PowerPoint. No cloud account,
model call, IAM change, or Gemini Enterprise registration is involved.

Press Ctrl+C to stop the preview and its worker. Saved jobs remain under
`.tmp/demo/18092/`. A different port uses separate state:

```bash
uv run --frozen --extra mcp-app python scripts/dev_demo.py --port 18192
```

## Make a change and see it

Edit the widget in `mcp-app/src/app.ts` and `mcp-app/src/style.css`. The local
host controls are in `mcp-app/src/local-host.ts` and `mcp-app/host.html`.

```bash
mise run build-ui
```

Reload the browser. Use the host's **Chat width** and **Theme** controls to
check the same artifact under different host constraints. These controls
are local development tools, not additions to Gemini Enterprise.

## Diagnose setup

```bash
mise run doctor
python scripts/doctor.py --json
```

The diagnostic checks local tools, isolated Python environments, four slide
skills, the Chromium binary, and built UI files. Each failure includes a repair
command. It does not read `.env`, print credentials, or contact cloud APIs.
On a minimal Linux image, Chromium may also need system libraries:

```bash
uv run --extra mcp-app playwright install --with-deps chromium
```

That command may require administrator privileges. Missing cloud credentials
are not a failure for the simulation.

## Verify real generation separately

For actual authoring, configure `.env` with your approved project, renderer,
and bucket using [.env.example](../.env.example) as the reference. Then run:

```bash
mise run dev
```

Open `http://127.0.0.1:18093`. This mode makes model and renderer calls and can
incur charges. Local state stays in `.tmp/dev-local/18093/`. Never copy local
credentials, generated decks, or job databases into Git.

For A2A, use [the workstation guide](workstation-preview.md). For the final
signed-in MCP test, follow [the Gemini Enterprise setup guide](mcp-setup.md).
A local iframe is not evidence that the tenant accepts the integration.

## What we take from Open Design

[Open Design](https://github.com/nexu-io/open-design#product-tour) keeps the
brief, generated files, preview, and refinement actions together. Here that
means testing the actual MCP widget throughout development, making drafts
visible before completion, and keeping failures recoverable.

Gemini Enterprise remains the host. We retain its A2UI catalog and the
official MCP bridge. We do not import Open Design's desktop runtime,
general-purpose skill catalog, or a second application shell.
