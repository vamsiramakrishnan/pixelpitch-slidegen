"""Download a completed local-queue deck and save source HTML without credentials.

Reads an explicit SQLite job and Cloud Storage object. Never submits generation,
modifies job state, deploys, or includes signed download URLs in the evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from google.cloud import storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        job = db.execute("SELECT * FROM jobs WHERE id=?", (args.job,)).fetchone()
        if not job or job["status"] != "completed":
            raise ValueError("The selected job is not completed")
        result = json.loads(job["result"])
        slides = db.execute(
            "SELECT slide_index,title,html FROM slides WHERE job=? ORDER BY slide_index",
            (args.job,),
        ).fetchall()
    uri = result.get("gs_uri", "")
    if not uri.startswith("gs://"):
        raise ValueError("No Cloud Storage artifact in the completed job")
    bucket_name, _, object_name = uri[5:].partition("/")
    blob = storage.Client().bucket(bucket_name).blob(object_name)
    blob.reload()
    content = blob.download_as_bytes(if_generation_match=blob.generation)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "deck.pptx").write_bytes(content)
    (out / "slides").mkdir(exist_ok=True)
    for slide in slides:
        (out / "slides" / f"{slide['slide_index'] + 1:02d}.html").write_text(
            slide["html"]
        )
    report = {
        "job": args.job,
        "brief": json.loads(job["brief"]),
        "seconds": round(job["finished"] - job["created"], 3),
        "slide_count": result.get("slide_count"),
        "source_html_count": len(slides),
        "gs_uri": uri,
        "generation": str(blob.generation),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "template_bundle_id": result.get("template_bundle_id"),
    }
    (out / "provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
