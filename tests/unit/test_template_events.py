import base64
import hashlib
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound, PreconditionFailed
import pytest

from app import template_versions as versions
from app import template_worker as worker
from app.template_queue import TemplateQueue, enqueue, task_body
from app.template_versions import TemplateVersion, publish_bundle, ready_prefix


class Bucket:
    def __init__(self):
        self.files = {}
        self.fail_on = None

    def blob(self, name):
        bucket = self

        class Blob:
            def __init__(self):
                self.name = name

            def exists(self):
                return name in bucket.files

            def download_as_bytes(self):
                if name not in bucket.files:
                    raise NotFound(name)
                return bucket.files[name]

            def upload_from_string(self, content, *, if_generation_match=None, **kwargs):
                if bucket.fail_on == name:
                    raise RuntimeError("interrupted upload")
                if if_generation_match == 0 and name in bucket.files:
                    raise PreconditionFailed(name)
                bucket.files[name] = content.encode() if isinstance(content, str) else content

        return Blob()


def version(generation="12", name="templates/company.pptx"):
    return TemplateVersion("deck-bucket", name, generation)


def fake_manifest(label):
    identity = hashlib.sha256(label.encode()).hexdigest()
    return SimpleNamespace(bundle_id=identity, to_json=lambda: json.dumps({"bundle_id": identity}), to_dict=lambda: {})


def test_generation_identity_is_stable_and_does_not_use_a_lossy_filename_slug():
    assert version().task_id == version().task_id
    assert version().prefix != version("13").prefix
    assert version(name="templates/Copy of company.pptx").prefix != version().prefix
    assert version().is_source("deck-bucket")


@pytest.mark.parametrize("name", ["templates/company.pdf", "templates/company/prepared/template.pptx", "decks/output.pptx", "prepared-templates/a/template.pptx"])
def test_outputs_and_non_pptx_files_cannot_trigger_preparation(name):
    assert not version(name=name).is_source("deck-bucket")


def test_newer_and_older_jobs_never_publish_to_the_same_ready_marker(monkeypatch):
    monkeypatch.setattr(versions, "validate_prepared_manifest", lambda *args, **kwargs: None)
    bucket = Bucket()
    newer = publish_bundle(bucket, version("13"), fake_manifest("new"), {"a": b"new"})
    publish_bundle(bucket, version("12"), fake_manifest("old"), {"a": b"old"})
    assert ready_prefix(bucket, version("13")) == newer


def test_partial_upload_is_not_ready_and_retry_converges(monkeypatch):
    monkeypatch.setattr(versions, "validate_prepared_manifest", lambda *args, **kwargs: None)
    bucket, manifest = Bucket(), fake_manifest("one")
    bucket.fail_on = version().prefix + manifest.bundle_id + "/b"
    with pytest.raises(RuntimeError):
        publish_bundle(bucket, version(), manifest, {"a": b"one", "b": b"two"})
    assert ready_prefix(bucket, version()) is None
    bucket.fail_on = None
    first = publish_bundle(bucket, version(), manifest, {"a": b"one", "b": b"two"})
    assert publish_bundle(bucket, version(), manifest, {"a": b"one", "b": b"two"}) == first


def test_duplicate_valid_attempts_keep_the_first_committed_bundle(monkeypatch):
    monkeypatch.setattr(versions, "validate_prepared_manifest", lambda *args, **kwargs: None)
    bucket = Bucket()
    first = publish_bundle(bucket, version(), fake_manifest("first"), {"a": b"one"})
    assert publish_bundle(bucket, version(), fake_manifest("second"), {"a": b"two"}) == first
    assert bucket.files[first + "a"] == b"one"


def test_task_pins_generation_and_uses_oidc_with_a_stable_name():
    config = TemplateQueue("projects/test/locations/us-central1/queues/templates", "https://worker.run.app", "tasks@test.iam.gserviceaccount.com")
    task = task_body(version(), config)["task"]
    assert task["name"].endswith(version().task_id)
    assert json.loads(base64.b64decode(task["httpRequest"]["body"])) == version().to_dict()
    assert task["httpRequest"]["oidcToken"]["audience"] == config.worker_url
    assert task["dispatchDeadline"] == "1800s"
    assert task_body(version(), config, retry_id="retry-2")["task"]["name"] != task["name"]


def test_cloud_tasks_duplicate_is_acknowledged(monkeypatch):
    config = TemplateQueue("projects/test/locations/us-central1/queues/templates", "https://worker.run.app", "tasks@test.iam.gserviceaccount.com")
    monkeypatch.setattr(TemplateQueue, "from_env", lambda: config)
    response = SimpleNamespace(status_code=409)
    session = SimpleNamespace(post=lambda *args, **kwargs: response)
    assert enqueue(version(), session=session).endswith(version().task_id)


def test_event_handler_enqueues_without_running_the_model(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")
    calls = []
    monkeypatch.setattr(worker, "enqueue", lambda value: calls.append(value) or value.task_id)
    monkeypatch.setattr(worker, "prepare_version", lambda _: pytest.fail("must not prepare in delivery request"))
    response = TestClient(worker.app).post("/events", headers={"ce-type": "google.cloud.storage.object.v1.finalized"}, json=version().to_dict())
    assert response.status_code == 200 and calls == [version()]


def test_event_handler_ignores_generated_files(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")
    monkeypatch.setattr(worker, "enqueue", lambda _: pytest.fail("output cannot enqueue"))
    response = TestClient(worker.app).post("/events", headers={"ce-type": "google.cloud.storage.object.v1.finalized"}, json=version(name="templates/company/prepared/template.pptx").to_dict())
    assert response.status_code == 204


def test_event_queue_failure_requests_redelivery(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")
    def fail(_):
        raise RuntimeError("queue offline")
    monkeypatch.setattr(worker, "enqueue", fail)
    response = TestClient(worker.app).post("/events", headers={"ce-type": "google.cloud.storage.object.v1.finalized"}, json=version().to_dict())
    assert response.status_code == 503


def test_superseded_generation_does_not_start_authoring(monkeypatch):
    bucket = Bucket()
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")
    monkeypatch.setattr(worker.storage, "Client", lambda: SimpleNamespace(bucket=lambda _: bucket))
    monkeypatch.setattr(worker, "is_current", lambda *_: False)
    assert worker.prepare_version(version()) == {"state": "superseded"}
    assert json.loads(bucket.files[version().status_path])["state"] == "superseded"


def test_ready_bundle_does_not_repeat_authoring(monkeypatch):
    bucket = Bucket()
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")
    monkeypatch.setattr(worker.storage, "Client", lambda: SimpleNamespace(bucket=lambda _: bucket))
    monkeypatch.setattr(worker, "is_current", lambda *_: True)
    monkeypatch.setattr(worker, "ready_prefix", lambda *_: "committed/")
    assert worker.prepare_version(version()) == {"state": "ready", "bundle_prefix": "committed/"}
