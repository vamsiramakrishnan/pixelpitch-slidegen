import io
import zipfile

from fastapi.testclient import TestClient

from renderer.main import app


def test_preparation_reads_the_pinned_generation_and_streams_an_archive(monkeypatch):
    from google.cloud import storage

    calls = []

    class Blob:
        size = 10

        def reload(self):
            pass

        def download_to_filename(self, target, **kwargs):
            calls.append(kwargs)
            target.write_bytes(b"source")

    class Bucket:
        def blob(self, key, generation):
            calls.append((key, generation))
            return Blob()

    class Client:
        def bucket(self, _name):
            return Bucket()

    monkeypatch.setattr(storage, "Client", Client)
    monkeypatch.setattr("renderer.template_source.prepare_source", lambda *_: {"source-extraction.json": b"{}"})
    response = TestClient(app).post("/template-source", json={"gs_uri": "gs://bucket/template.pptx", "generation": "42"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert calls == [("template.pptx", 42), {"if_generation_match": 42}]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("source-extraction.json") == b"{}"


def test_preparation_requires_the_same_renderer_auth_as_conversion(monkeypatch):
    monkeypatch.setenv("RENDERER_API_KEY", "test-only")
    response = TestClient(app).post("/template-source", json={"gs_uri": "gs://bucket/template.pptx", "generation": "42"})
    assert response.status_code == 401
