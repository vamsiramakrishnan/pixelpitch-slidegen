import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "slidegen_doctor", ROOT / "scripts/doctor.py"
)
doctor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = doctor
spec.loader.exec_module(doctor)


def test_missing_python_gives_a_repair_command(tmp_path):
    result = doctor.python_check(
        tmp_path, "App", ".venv/bin/python", "pass", "mise run setup-dev"
    )
    assert not result.ok
    assert result.fix == "mise run setup-dev"


def test_python_probe_checks_the_real_exit_code(tmp_path):
    result = doctor.python_check(
        tmp_path, "Python", sys.executable, "raise SystemExit(1)", "repair"
    )
    assert not result.ok
    assert result.detail == "Dependency check failed"


def test_python_probe_does_not_echo_sensitive_stderr(tmp_path):
    result = doctor.python_check(
        tmp_path,
        "Python",
        sys.executable,
        "import sys; sys.exit('private value')",
        "repair",
    )
    assert "private value" not in result.detail
