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

"""Both directions, because the false positive is the more expensive one.

A guard that misses lets an invented plate through, which is the defect this
exists for. A guard that fires on a correct call is worse in practice: the
pipeline's own `deck spec` and `deck plates` write these very directories, so
a blanket rule would break the thing it protects, and an author refused once
on a correct call stops reading refusals.
"""

from __future__ import annotations

import pytest
from guards import WRITE_TOOLS, sealed_roots, writes_into_sealed


@pytest.fixture
def workspace(tmp_path):
    """A staged workspace, sealed or not as the case under test needs."""

    def stage(sealed: bool = True):
        for name in ("clean", "spec-out", "slides"):
            (tmp_path / name).mkdir()
        (tmp_path / "clean" / "slide-01.png").write_bytes(b"\x89PNG")
        if sealed:
            (tmp_path / "layouts.json").write_text("{}")
        return tmp_path

    return stage


def denied(root, **args) -> bool:
    return writes_into_sealed(args, sealed_roots([str(root)]), cwd=root)


def test_compiled_source_seals_baselines_before_catalog_authoring(workspace):
    root = workspace(sealed=False)
    (root / "source-extraction.json").write_text("{}")
    (root / "baselines").mkdir()
    assert root / "baselines" in sealed_roots([root])
    assert denied(root, path="baselines/source-26.html")
    assert denied(root, path="spec-out/spec/slide-26.json")
    assert not denied(root, path="catalog/example.html")


# --------------------------------------------------------------------------
# the seal


def test_an_unsealed_template_is_still_being_built(workspace):
    """`deck spec` and `deck plates` write these directories. Until the
    contract pins them there is nothing to protect and everything to produce."""
    root = workspace(sealed=False)
    assert sealed_roots([str(root)]) == []
    assert not denied(root, file_path="clean/navy-statement.png")


def test_the_contract_is_what_seals_it(workspace):
    root = workspace()
    assert {p.name for p in sealed_roots([str(root)])} == {"clean", "spec-out"}


# --------------------------------------------------------------------------
# writes


def test_an_invented_plate_is_refused(workspace):
    """The exact defect. Three of these shipped, and every gate downstream
    passed them because each slide had an image layer."""
    root = workspace()
    assert denied(root, file_path="clean/navy-statement.png")
    assert denied(root, TargetFile=str(root / "clean" / "blue-statement.png"))


def test_a_generated_image_is_refused_too(workspace):
    """An invented flat ground is exactly what an image generator produces
    when a turn decides the template lacks a colour it wants."""
    root = workspace()
    assert denied(root, output_path="clean/white-content.png")


def test_overwriting_a_pinned_plate_is_refused(workspace):
    root = workspace()
    assert denied(root, file_path="clean/slide-01.png")


def test_the_extracted_geometry_is_refused_too(workspace):
    """spec-out/ is what PowerPoint said. A turn that edits it is editing the
    evidence, and every plate hash was measured against it."""
    root = workspace()
    assert denied(root, file_path="spec-out/spec/slide-07.json")


def test_a_redirect_into_a_sealed_directory_is_refused(workspace):
    root = workspace()
    assert denied(root, CommandLine="python3 make.py > clean/slide-09.png")


def test_re_running_plates_over_a_sealed_clean_is_refused(workspace):
    """Not pedantry. The plates would no longer hash to what the contract
    pinned, which is the `overwritten since precompute` finding — caught here
    one tool call before it becomes a finding."""
    root = workspace()
    assert denied(root, CommandLine="deck plates --spec spec-out --out clean")


def test_a_copy_into_a_sealed_directory_is_refused(workspace):
    root = workspace()
    assert denied(root, CommandLine="cp /tmp/ground.png clean/slide-02.png")


# --------------------------------------------------------------------------
# reads and everything else


def test_only_the_tools_that_write_are_guarded():
    """A path argument means a write on `create_file` and a read on
    `view_file`, and the predicate cannot tell them apart — so it is never
    asked about the reading ones. Every slide on the roundtrip route points at
    a plate; a guard that denied that would deny the route."""
    assert "view_file" not in WRITE_TOOLS
    assert set(WRITE_TOOLS) == {
        "create_file", "edit_file", "generate_image", "run_command"
    }


def test_a_command_that_only_reads_a_plate_is_allowed(workspace):
    root = workspace()
    assert not denied(root, CommandLine="deck diff --a out/s3.png --b clean/slide-03.png")
    assert not denied(root, CommandLine="python3 -c \"print(open('clean/x').read())\"")


def test_authoring_a_slide_that_points_at_a_plate_is_allowed(workspace):
    root = workspace()
    assert not denied(root, file_path="slides/07-customer-care.html")


def test_the_contract_itself_can_be_rebuilt(workspace):
    """`deck layouts --out layouts.json` writes beside the sealed directories,
    not into them. Re-sealing has to stay possible or the plates can never be
    legitimately repaired."""
    root = workspace()
    assert not denied(
        root, CommandLine="deck layouts --spec spec-out --clean clean --out layouts.json"
    )


def test_another_workspaces_clean_is_not_this_ones(workspace, tmp_path):
    root = workspace()
    other = tmp_path / "elsewhere" / "clean"
    other.mkdir(parents=True)
    assert not denied(root, file_path=str(other / "slide-01.png"))


def test_a_call_with_no_arguments_is_not_a_write(workspace):
    root = workspace()
    assert not denied(root)
    assert not writes_into_sealed("not a dict", sealed_roots([str(root)]))


def test_an_unparseable_command_does_not_crash_the_predicate(workspace):
    """A predicate that raises fails the tool call open or closed depending on
    the evaluator's mood, and either is worse than reading no targets."""
    root = workspace()
    assert not denied(root, CommandLine="echo 'unbalanced")
