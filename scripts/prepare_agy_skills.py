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

"""Validate the four bundled slide-generation skills without copying a catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / "agy-worker" / "skills"
REQUIRED_SKILLS = frozenset(
    {
        "pixelpitch-brand-derivation",
        "pixelpitch-deck",
        "pixelpitch-slide-craft",
        "pixelpitch-template-roundtrip",
    }
)


def prepare(*, check: bool = False) -> dict:
    """Both legacy command forms now perform the same read-only validation."""
    expected = {f"{name}/SKILL.md" for name in REQUIRED_SKILLS}
    actual = {
        path.relative_to(SKILLS_ROOT).as_posix()
        for path in SKILLS_ROOT.rglob("SKILL.md")
        if path.is_file()
    }
    if actual != expected:
        raise RuntimeError(
            "Bundled slide skills do not match the allowlist: "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}. "
            "Use SLIDEGEN_AGY_SKILLS_PATHS to opt into additional skills."
        )

    for path in SKILLS_ROOT.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Bundled skills must not contain symlinks: {path}")

    return {
        "source": str(SKILLS_ROOT),
        "skills": len(REQUIRED_SKILLS),
        "names": sorted(REQUIRED_SKILLS),
        "status": "validated",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the bundled skills, also the default behavior",
    )
    args = parser.parse_args()
    print(json.dumps(prepare(check=args.check), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
