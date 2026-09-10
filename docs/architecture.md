# How Pixelpitch fits together

Pixelpitch has two separate kinds of work. Deck generation starts when a user
submits a brief. Template preparation starts when a source PowerPoint changes.
Preparing templates ahead of time removes that work from the first deck request.

![Deck generation and upload-triggered template preparation](assets/architecture.svg)

## A user creates a deck

1. Gemini Enterprise opens the MCP App inside the conversation.
2. The private MCP API checks the signed-in user and saves the brief as a job in Firestore.
3. A separate deck worker claims that job. The chat request does not need to
   stay open while the worker runs.
4. The worker uses the shared ADK agent and isolated Antigravity process to author slides.
5. The renderer converts the slides to PowerPoint and checks the exported content.
6. The renderer saves the PowerPoint in Cloud Storage. The deck worker saves
   available export previews. The widget reads progress through the API and
   offers the download when it is ready.

Draft HTML and images live in private Cloud Storage, not inside large Firestore
documents. The widget does not call Vertex or Storage with embedded credentials.
It shows recorded progress, not invented percentages or completion estimates.

The optional A2A agent is another entry point to the same authoring and renderer
code. It has its own GE registration and A2UI cards. The standalone Cloud Run
A2A path is request-bound. The separate saved-job A2A experiment runs on a
workstation and must not be confused with the durable MCP cloud worker.

## A template file changes

Suppose you replace `gs://YOUR_DECK_BUCKET/templates/company.pptx`. The address
stays the same, but Cloud Storage assigns a new version number called a
*generation*. The preparation job records that number so it parses the exact
file that was uploaded.

Eventarc delivers the notification to `/events` on the private template worker.
That endpoint checks the file and adds a Cloud Task. It returns without parsing
the presentation.

Cloud Tasks calls `/prepare` on the same service. The worker uses the renderer
to extract every slide's text positions, typography, table content, and artwork.
Antigravity prepares the layout catalog. After validation, the worker saves the
prepared files and writes `ready.json` last.

Both A2A and MCP read that ready bundle. If a newer file arrives while the old
one is being parsed, the old job cannot replace the new version's bundle.
Changing the source does not change PowerPoints already generated from it.

The template worker is not the MCP deck worker. Template jobs use Cloud Tasks.
MCP deck jobs use Firestore. Their queues have different jobs and lifetimes.

See [Template architecture](template-architecture.md) for retries and versioning,
or [Template upload and recovery](template-operations.md) for setup commands.

## What deployment installs

`mise run configure` selects `a2a`, `mcp`, or `both`. Full deployment installs
shared resources and the selected interfaces. With `automatic` preparation,
it also installs the Eventarc trigger, Cloud Tasks queue, and template worker.

`mise run deploy-mcp` adds only MCP to an existing renderer. It does not install
the template trigger. `mise run deploy-templates` adds that pipeline and connects
the interfaces selected in `deploy.env`.

All values come from your configuration. The runtime project owns the services,
queues, and storage. The GE app can be in another project. Its service identity
gets permission to call the frontend, not blanket access to all services.

## Authentication and output limits

Cloud Run checks the GE service identity. The MCP API separately checks the
user's OAuth token, client ID, and allowed email domain. The OAuth secret stays
in the GE connector configuration. The A2A registration does not inherit MCP OAuth.

Template reconstruction preserves source positions and artwork but does not
transplant native PowerPoint masters. Some tables can be reconstructed as
editable text cells rather than native PowerPoint tables. Inspect the actual
export, not only a preview or service health check.

Start with [MCP setup](mcp-setup.md) or [Cloud Shell deployment](../CLOUD_SHELL.md).
