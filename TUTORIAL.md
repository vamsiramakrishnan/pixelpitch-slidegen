# Deploy from Cloud Shell

Use your own project and approved release checkout. No project ID or customer
tenant is supplied by this repository. Deployment creates billable resources
and requires permission to manage the services and their scoped IAM grants.

## Choose your setup guide

For the interactive widget, follow [Set up the MCP App](docs/mcp-setup.md).
It covers the OAuth client, Cloud Shell commands, GE connector, and first real test.

For A2A or both interfaces, follow [Cloud Shell deployment](CLOUD_SHELL.md).
These are the maintained procedures. This page does not duplicate their commands.

## Prepare templates automatically

Choose `automatic` during configuration to install the Eventarc upload trigger
as part of a new full deployment. For an existing installation, follow
[Template upload and recovery](docs/template-operations.md).

Uploading or replacing a `.pptx` queues preparation. Wait for the new file
version's `ready: true` result before selecting it in GE. See
[Architecture](docs/architecture.md) for the diagram and plain-language explanation.

## Verify before release

A healthy Cloud Run service is not a completed integration. Run the signed-in
GE test from your setup guide, download a real PowerPoint, and inspect the result.
Keep credentials, state, logs, and customer files out of the public repository.
