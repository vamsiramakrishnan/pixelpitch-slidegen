"""Offline source-to-export proof. Writes locally; never uploads or deploys.

Use the agent interpreter. The converter runs in the isolated renderer venv.
--author additionally exercises the real AGY template-authoring turn, with
only the prepared-bundle storage boundary supplied from a local archive.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import deck_run
from app.template_baselines import compile_source_baselines
from app.template_policy import SourceIdentity, build_prepared_manifest, sha256_bytes


VERIFY_EXPORT = """
from pathlib import Path
import json
import sys
from renderer.content_check import missing_content
from renderer.main import _quality_report

out = Path(sys.argv[1])
failures = missing_content(
    [slide.read_text() for slide in sorted((out / 'slides').glob('*.html'))],
    out / 'deck.pptx',
)
quality = _quality_report(out / 'conversion.json', out / 'deck.pptx')
print(json.dumps({'content_failures': failures, 'quality': quality}, indent=2))
assert not failures, 'Authored content is missing from the PowerPoint'
assert quality.get('editability_passed') is True, 'Editability admission failed'
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--author", action="store_true")
    parser.add_argument(
        "--profile", choices=("full", "balanced", "fast"), default="full",
        help="Renderer profile; default uses the renderer's full fidelity pass",
    )
    parser.add_argument(
        "--replay-patches",
        type=Path,
        help="Replay an earlier local authoring result against newly compiled shells",
    )
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.archive) as archive:
        artifacts = {name: archive.read(name) for name in archive.namelist()}
    artifacts["template.pptx"] = args.source.read_bytes()
    artifacts["brand-contract.json"] = args.contract.read_bytes()
    contract = json.loads(artifacts["brand-contract.json"])
    protected = {
        int(n) for item in contract.get("protected", []) for n in item.get("slides", [])
    }
    artifacts.update(compile_source_baselines(artifacts, protected))
    source_uri = "gs://local-proof/template.pptx"
    manifest = build_prepared_manifest(
        source=SourceIdentity(
            gs_uri=source_uri,
            generation="1",
            etag="local-proof",
            sha256=sha256_bytes(artifacts["template.pptx"]),
        ),
        artifacts=artifacts,
    )
    (out / "manifest.json").write_text(manifest.to_json())

    def stage(_slug, uri, workdir):
        assert uri == source_uri
        for name, value in artifacts.items():
            target = deck_run._local_artifact_path(workdir, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        (workdir / "manifest.json").write_text(manifest.to_json())
        return manifest, None

    if args.replay_patches:
        stage("", source_uri, out)
        document = json.loads(args.replay_patches.read_text())
        types = {item.id: item for item in manifest.slide_types}
        for slide in document["slides"]:
            baseline = artifacts[types[slide["slide_type_id"]].baseline_path]
            slide["baseline_sha256"] = sha256_bytes(baseline)
        (out / "patches.json").write_text(json.dumps(document))
        slides, error = deck_run.harvest_patches(out, manifest, len(document["slides"]))
        if error:
            raise RuntimeError(error)
    elif args.author:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
        with patch.object(deck_run, "_fetch_prepared_bundle", stage):
            result = deck_run.run_template_deck(
                topic="Template fidelity proof: one cover, one table, and one body slide",
                audience="Internal engineering review",
                goal="Use source-01 for the cover and source-26 or source-27 for two body slides. One body must contain a native HTML table with headers Check and Outcome and rows Layout / Preserved and Text / Editable. Explain that this is a local template-fidelity test. No product benchmarks, statistics, or invented facts. Keep original type, artwork, and text zones.",
                slide_count=3,
                template_name="Local fidelity proof",
                template_gs_uri=source_uri,
                on_event=lambda event: print(
                    json.dumps(event, default=str), flush=True
                ),
            )
        if result.get("status") != "ok":
            raise RuntimeError(result.get("error"))
        slides = result["slides"]
        print("AUTHORING_WORKSPACE", result["workspace"], flush=True)
    else:
        stage("", source_uri, out)
        slides = []
        for slide_type in manifest.slide_types:
            if slide_type.source_slide not in {1, 10, 26, 27}:
                continue
            path = out / slide_type.baseline_path
            slides.append(
                {
                    "title": slide_type.id,
                    "html": deck_run._inline_local_images(
                        path.read_text(), base_dir=path.parent, workdir=out
                    ),
                }
            )
    slide_dir = out / "slides"
    slide_dir.mkdir(exist_ok=True)
    for index, slide in enumerate(slides, 1):
        (slide_dir / f"{index:02d}.html").write_text(slide["html"])
    renderer_python = ROOT / "renderer/.venv/bin/python"
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "vendor/slidify")
        + os.pathsep
        + str(ROOT / "renderer"),
        PATH=str(renderer_python.parent) + os.pathsep + os.environ.get("PATH", ""),
        SLIDIFY_PROFILE=args.profile,
    )
    subprocess.run(
        [
            str(renderer_python),
            "-c",
            "from renderer.main import _run_slidify; from pathlib import Path; import sys; ok,error=_run_slidify(*map(Path,sys.argv[1:])); print(error or 'Conversion finished'); sys.exit(0 if ok else 1)",
            str(slide_dir),
            str(out / "deck.pptx"),
            str(out / "conversion.json"),
        ],
        env=env,
        check=True,
    )
    subprocess.run(
        [
            str(renderer_python),
            "-c",
            VERIFY_EXPORT,
            str(out),
        ],
        env=env,
        check=True,
    )
    print("LOCAL_PPTX_PROOF", out / "deck.pptx")


if __name__ == "__main__":
    main()
