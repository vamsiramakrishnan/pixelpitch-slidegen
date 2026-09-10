"""The deployable authoring catalog stays small and self-contained."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def skill_validator(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("prepare_agy_skills")


def make_skills(skill_validator, tmp_path, monkeypatch):
    root = tmp_path / "skills"
    for name in skill_validator.REQUIRED_SKILLS:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    monkeypatch.setattr(skill_validator, "SKILLS_ROOT", root)
    return root


def test_checked_in_catalog_contains_exactly_four_slide_skills(skill_validator):
    assert skill_validator.prepare(check=True)["names"] == [
        "pixelpitch-brand-derivation",
        "pixelpitch-deck",
        "pixelpitch-slide-craft",
        "pixelpitch-template-roundtrip",
    ]


def test_prepare_is_read_only_and_does_not_touch_old_mirror(
    skill_validator, tmp_path, monkeypatch
):
    root = make_skills(skill_validator, tmp_path, monkeypatch)
    mirror = tmp_path / "repository-skills"
    mirror.mkdir()
    sentinel = mirror / "unrelated.txt"
    sentinel.write_text("preserve source data")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    first = skill_validator.prepare()
    second = skill_validator.prepare(check=True)

    assert first == second == {
        "source": str(root),
        "skills": 4,
        "names": sorted(skill_validator.REQUIRED_SKILLS),
        "status": "validated",
    }
    assert before == {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }


def test_validator_rejects_missing_skill(skill_validator, tmp_path, monkeypatch):
    monkeypatch.setattr(skill_validator, "SKILLS_ROOT", tmp_path / "absent")
    with pytest.raises(RuntimeError, match=r"missing=.*pixelpitch-deck"):
        skill_validator.prepare(check=True)


@pytest.mark.parametrize("extra", ["unrelated", "pixelpitch-deck/nested"])
def test_validator_rejects_extra_discoverable_skills(
    skill_validator, tmp_path, monkeypatch, extra
):
    root = make_skills(skill_validator, tmp_path, monkeypatch)
    directory = root / extra
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("unexpected skill")
    with pytest.raises(RuntimeError, match=r"unexpected=.*SKILL\.md"):
        skill_validator.prepare(check=True)


def test_validator_rejects_symlinked_payload(skill_validator, tmp_path, monkeypatch):
    root = make_skills(skill_validator, tmp_path, monkeypatch)
    external = tmp_path / "outside.txt"
    external.write_text("external payload")
    (root / "pixelpitch-deck" / "external.txt").symlink_to(external)
    with pytest.raises(RuntimeError, match="must not contain symlinks"):
        skill_validator.prepare()
