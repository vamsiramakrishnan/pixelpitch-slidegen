# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Brand derivation orchestration: stage a template, let the harness derive.

The understanding — reading ``ppt/theme/theme1.xml`` for exact tokens and
inspecting the template's rendered slides for its visual language — belongs to
the Antigravity harness, driven by the ``pixelpitch-brand-derivation`` skill.
This module is deliberately only orchestration:

1. stage: fetch the template from GCS and rasterise its slides (mechanical
   I/O via gsutil / soffice / pdftoppm);
2. one harness turn inside the staged workspace (unlimited budget, scoped
   file policies, headless interaction hook — all configured in the runner);
3. read back and validate ``brand-contract.json``.

No model or vision SDK is imported here. The harness is the intelligence.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from app.agy_authoring import generate as generate_with_agy

logger = logging.getLogger(__name__)

DERIVER_MODEL = os.getenv("SLIDEGEN_REFERENCE_MODEL", "gemini-3.7-flash")

CONTRACT_NAME = "brand-contract.json"
_REQUIRED_KEYS = (
    "palette",
    "palette_source",
    "fonts",
    "accent_rules",
    "layout_worlds",
    "decorative_devices",
    "type_hierarchy",
    "photo_treatment",
    "visual_aids_style",
    "protected",
    "dos",
    "donts",
    "design_md",
)

_SYSTEM = """\
You are a brand analyst inside an agent harness (shell, files, subagents,
image inspection). A customer PowerPoint template is staged in this workspace
as `template.pptx`, with its slides rendered under `refs/`.

Derive its brand contract by applying the `pixelpitch-brand-derivation` skill:
read the theme XML for exact colour and font tokens, inspect every rendered
slide for the visual language, map colours to usage roles, record protected
material, and write `brand-contract.json` to the workspace root exactly per the
skill's contract schema. Use as many steps and subagents as the derivation
needs; there is no budget.

End with exactly one line: WROTE brand-contract.json
"""


def _run(cmd: list[str], timeout: int) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"staging command failed ({cmd[0]}): {(proc.stderr or '')[-400:]}"
        )


_STAGE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "agy-worker"
    / "skills"
    / "pixelpitch-brand-derivation"
    / "scripts"
    / "stage_template.sh"
)


def _stage(template_gs_uri: str, workdir: Path) -> int:
    """Stage via the skill's own script — the single source of truth.

    The script (fetch, convert, rasterise) lives beside the skill it serves,
    so the harness and this orchestrator run identical staging steps.
    """
    if not _STAGE_SCRIPT.is_file():
        raise RuntimeError(f"staging script missing: {_STAGE_SCRIPT}")
    workdir.mkdir(parents=True, exist_ok=True)
    _run(["bash", str(_STAGE_SCRIPT), template_gs_uri, str(workdir)], timeout=600)
    return len(sorted((workdir / "refs").glob("slide*.png")))


def derive_brand(
    *,
    template_gs_uri: str,
    workdir: str | os.PathLike[str],
    model: str | None = None,
) -> dict:
    """Derive and return the brand contract for ``template_gs_uri``.

    Raises ``RuntimeError`` on staging failure, harness failure, or a missing
    or invalid contract.
    """
    work = Path(workdir)
    pages = _stage(template_gs_uri, work)
    logger.info("staged template + %d rendered pages in %s", pages, work)

    contract_path = work / CONTRACT_NAME
    contract_path.unlink(missing_ok=True)

    prior = os.environ.get("SLIDEGEN_AGY_WORKSPACES")
    os.environ["SLIDEGEN_AGY_WORKSPACES"] = str(work.resolve())
    try:
        text = generate_with_agy(
            contents=(
                "Derive the brand contract for the staged customer template "
                "and write brand-contract.json. Derivation is complete only "
                "when every theme role is accounted for and every rendered "
                "slide has been inspected."
            ),
            system=_SYSTEM,
            model=model or DERIVER_MODEL,
        )
    finally:
        if prior is None:
            os.environ.pop("SLIDEGEN_AGY_WORKSPACES", None)
        else:
            os.environ["SLIDEGEN_AGY_WORKSPACES"] = prior

    if not contract_path.is_file():
        raise RuntimeError(
            f"harness finished without writing {CONTRACT_NAME}: {text[-400:]}"
        )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    missing = [key for key in _REQUIRED_KEYS if key not in contract]
    if missing:
        raise RuntimeError(f"brand contract missing keys: {missing}")
    logger.info(
        "brand contract derived: %d palette roles, %d worlds",
        len(contract.get("palette") or {}),
        len(contract.get("layout_worlds") or []),
    )
    return contract


if __name__ == "__main__":  # pragma: no cover - manual derivation entrypoint
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Derive a brand contract via the harness.")
    parser.add_argument("template_gs_uri")
    parser.add_argument("--workdir", default="/tmp/pixelpitch-brand")
    args = parser.parse_args()

    result = derive_brand(template_gs_uri=args.template_gs_uri, workdir=args.workdir)
    summary = {k: v for k, v in result.items() if k != "design_md"}
    print(json.dumps(summary, indent=2))
    print("\n--- design_md ---\n")
    print(result["design_md"])
