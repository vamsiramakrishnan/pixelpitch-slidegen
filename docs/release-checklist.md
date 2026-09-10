# Prepare a public release

Use this checklist on the exact snapshot you intend to publish. Documentation
checks are useful, but they do not review all code, customer files, or Git history.
Do not publish until every applicable check is complete.

## 1. Check the documentation and examples

From the repository root, run:

```bash
mise run docs-check
uv run pytest tests/unit tests/integration
```

Confirm that setup uses the customer's configuration. No runnable command
should default to a development tenant. The diagnostic `scripts/gen_deck.py`
requires an explicit `--server` for this reason.

Record failed and skipped checks. Do not describe local tests as proof of a
customer's OAuth consent, organization policy, or signed-in widget rendering.

## 2. Inspect the files Git will publish

Review tracked files and pending changes without modifying either:

```bash
git status --short
git ls-files -- artifacts .tmp .google-agents-cli deploy.env .env
git diff --stat
git diff --cached --stat
```

Review any listed evaluation traces, grading results, runtime state, and
configuration. An ignore rule does not remove a file already tracked by Git.
Keep diagnostic outputs privately. Exclude them from a new release snapshot.
Removing tracked files or changing history requires a separate reviewed change.

Scan the complete snapshot and any history you will publish using your approved
secret scanner. Review customer names, template files, generated decks, signed
URLs, OAuth credentials, service-account keys, and private endpoints separately.
If a credential was exposed, revoke or rotate it. Deleting its latest file is
not sufficient.

## 3. Check licenses and copied assets

Review licenses and notices for the converter, bundled slide skills, fonts, images,
and bundled examples. Confirm permission to redistribute every copied asset.
Do not include a customer template because it was used for a local test.
Preserve the notices required by each dependency.

## 4. Verify the release checkout

Test from a fresh clone of this repository. Run the builds without access to
the original Pixelpitch checkout or pre-existing virtual environments.
Check the [included components and source provenance](standalone-repository.md).

Use a separate checkout and Terraform state for an approved test deployment.
Follow the [MCP setup guide](mcp-setup.md) or [Cloud Shell guide](../CLOUD_SHELL.md).
Then verify a real PowerPoint download and the
[template overwrite flow](template-operations.md#6-verify-the-upload-to-deck-path).
Keep the resulting evidence private.

## 5. Approve publication

Review the destination owner, repository name, visibility, exact file snapshot,
and history strategy. Record unresolved limits in the release notes.
Creating a repository or pushing files is a separate action from updating docs.
