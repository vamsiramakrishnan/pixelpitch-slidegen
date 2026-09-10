"""Complete, read-only template extraction on the renderer's native toolchain."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from lxml import etree


def _extractor():
    scripts = Path(
        os.environ.get("PIXELPITCH_TEMPLATE_SCRIPTS", "/opt/pixelpitch-template-tools")
    )
    if not scripts.is_dir():
        scripts = (
            Path(__file__).resolve().parents[2]
            / "agy-worker/skills/pixelpitch-template-roundtrip/scripts"
        )
    spec = importlib.util.spec_from_file_location(
        "extract_slide_spec", scripts / "extract_slide_spec.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("template extraction tool is not packaged")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _without_slide_text(source: Path, target: Path) -> None:
    """Remove glyphs in OOXML, never paint over artwork in a rendered image."""
    with (
        zipfile.ZipFile(source) as original,
        zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as clean,
    ):
        for item in original.infolist():
            data = original.read(item)
            if item.filename.startswith("ppt/slides/slide") and item.filename.endswith(
                ".xml"
            ):
                node = etree.fromstring(data)
                for field in node.findall(
                    ".//{http://schemas.openxmlformats.org/drawingml/2006/main}fld"
                ):
                    if field.get("type") == "slidenum":
                        field.getparent().remove(field)
                for text in node.findall(
                    ".//{http://schemas.openxmlformats.org/drawingml/2006/main}t"
                ):
                    text.text = ""
                data = etree.tostring(
                    node, xml_declaration=True, encoding="UTF-8", standalone=True
                )
            elif item.filename.startswith(
                ("ppt/slideLayouts/", "ppt/slideMasters/")
            ) and item.filename.endswith(".xml"):
                node = etree.fromstring(data)
                for field in node.findall(
                    ".//{http://schemas.openxmlformats.org/drawingml/2006/main}fld"
                ):
                    if field.get("type") == "slidenum":
                        field.getparent().remove(field)
                data = etree.tostring(
                    node, xml_declaration=True, encoding="UTF-8", standalone=True
                )
            clean.writestr(item, data)


def prepare_source(source: Path, workdir: Path) -> dict[str, bytes]:
    """All source pages and resolved specs, including content near the end."""
    extractor = _extractor()
    spec_root = workdir / "spec-out"
    extracted = extractor.extract(source, spec_root, base_renders=False)
    count = len(extracted["slides"])
    if count > 100:
        raise ValueError("template exceeds the 100-slide preparation limit")
    from slidify.fonts import _installed_families

    def requested_fonts(value):
        if isinstance(value, dict):
            if (
                value.get("text")
                and isinstance(value.get("text"), str)
                and value.get("font")
            ):
                yield value["font"]
            for child in value.values():
                yield from requested_fonts(child)
        elif isinstance(value, list):
            for child in value:
                yield from requested_fonts(child)

    fonts = {
        font
        for path in (spec_root / "spec").glob("slide-*.json")
        for font in requested_fonts(json.loads(path.read_text()))
    }
    installed = _installed_families()
    missing_fonts = sorted(font for font in fonts if font.casefold() not in installed)
    if missing_fonts:
        raise ValueError(
            "Install source fonts on the renderer before preparation: "
            + ", ".join(missing_fonts)
        )
    extractor.extract(source, spec_root)
    clean_deck = workdir / "artwork.pptx"
    _without_slide_text(source, clean_deck)
    clean_dir = workdir / "clean"
    clean_dir.mkdir()
    with tempfile.TemporaryDirectory(prefix="template-clean-lo-") as profile:
        subprocess.run(
            [
                "soffice",
                f"-env:UserInstallation={Path(profile).as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(clean_dir),
                str(clean_deck),
            ],
            capture_output=True,
            check=True,
            timeout=420,
        )
    pdf = clean_dir / "artwork.pdf"
    if not pdf.is_file():
        raise RuntimeError("template artwork produced no PDF")
    subprocess.run(
        ["pdftoppm", "-png", "-scale-to", "1280", str(pdf), str(clean_dir / "slide")],
        capture_output=True,
        check=True,
        timeout=420,
    )
    for page in list(clean_dir.glob("slide-*.png")):
        page.rename(clean_dir / f"slide-{int(page.stem.split('-')[-1]):02d}.png")
    artifacts = {}
    for prefix, folder in (
        ("spec", spec_root / "spec"),
        ("base", spec_root / "base"),
        ("clean", clean_dir),
    ):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix != ".pdf":
                artifacts[f"{prefix}/{path.relative_to(folder).as_posix()}"] = (
                    path.read_bytes()
                )
    for number in range(1, count + 1):
        for path in (
            f"spec/slide-{number:02d}.json",
            f"base/slide-{number:02d}.png",
            f"clean/slide-{number:02d}.png",
        ):
            if path not in artifacts:
                raise RuntimeError(f"incomplete template extraction: {path}")
    evidence = {
        "schema_version": 2,
        "slide_count": count,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "plate_method": "ooxml-remove-slide-text",
        "artifacts": {
            name: hashlib.sha256(value).hexdigest() for name, value in artifacts.items()
        },
    }
    artifacts["source-extraction.json"] = json.dumps(evidence, indent=2).encode()
    return artifacts


def archive(artifacts: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, value in artifacts.items():
            bundle.writestr(name, value)
    return buffer.getvalue()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = prepare_source(args.source.resolve(), args.out.resolve())
    (args.out / "source.zip").write_bytes(archive(result))
    print(
        json.dumps({"artifacts": len(result), "bytes": sum(map(len, result.values()))})
    )
