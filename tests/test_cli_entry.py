"""رگرسیون قرارداد ورودی CLI — «python -m peyja» باید از هر مسیری کار کند.

درس تلخ هاست (۱۴۰۵/۰۹/۰۵): python -m peyja ⇒ «No module named peyja.__main__»
چون آن بسته __main__.py نداشت. این تست‌ها دیگر اجازه نمی‌دهند آن کلاس خطا
برگردد — و فرمان‌های مستندات (peyja.sh / python -m peyja / کنسول‌اسکریپت)
همه به یک نقطه وصل‌اند.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    pp = os.pathsep.join([str(REPO_ROOT / "packages"), str(REPO_ROOT)])
    env["PYTHONPATH"] = pp + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [sys.executable, "-m", "peyja", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_python_m_peyja_help_from_repo_root() -> None:
    r = _run(["--help"], cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr
    assert "doctor" in r.stdout


def test_python_m_peyja_works_from_any_cwd(tmp_path: Path) -> None:
    """حتی از /tmp هم باید کار کند (bootstrap مسیر ریشه در __main__)."""
    r = _run(["--help"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "selftest-webhook" in r.stdout


def test_selftest_webhook_help_via_module() -> None:
    r = _run(["selftest-webhook", "--help"], cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr


def test_peyja_sh_wrapper_invokes_same_entry(tmp_path: Path) -> None:
    """اسکریپت واحد peyja.sh هم باید به همان ورودی وصل باشد."""
    wrapper = REPO_ROOT / "scripts" / "peyja.sh"
    env = dict(os.environ)
    pp = os.pathsep.join([str(REPO_ROOT / "packages"), str(REPO_ROOT)])
    env["PYTHONPATH"] = pp + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PEYJA_VENV"] = ""  # از venv محیطی استفاده نکن
    r = subprocess.run(
        ["bash", str(wrapper), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert "doctor" in r.stdout
