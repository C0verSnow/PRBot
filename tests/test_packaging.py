"""Exercise the real packaging script with build tools replaced (remote CI only)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def packaging_workspace(tmp_path):
    root = tmp_path / "checkout with spaces"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/build_dmg.sh", root / "scripts/build_dmg.sh")
    for name in ("README.md", ".env.example"):
        shutil.copy2(ROOT / name, root / name)

    tools = root / "tools"
    tools.mkdir()
    stub = tools / "stub"
    stub.write_text(
        """#!/bin/bash
set -euo pipefail
tool="${0##*/}"
printf '%s\\n' "$tool" >> "$PACKAGING_TOOL_LOG"
case "$tool" in
  uname)
    if [[ "$1" == "-s" ]]; then echo Darwin; else echo arm64; fi
    ;;
  python) mkdir -p dist/PRBot.app ;;
  codesign) : ;;
  ditto) mkdir -p "$2" ;;
  hdiutil) touch "${@: -1}" ;;
  shasum) echo 'stub checksum' ;;
  *) exit 1 ;;
esac
"""
    )
    stub.chmod(0o755)
    for name in ("uname", "python", "codesign", "ditto", "hdiutil", "shasum"):
        (tools / name).symlink_to(stub)
    return root


def run_packaging(root):
    return subprocess.run(
        ["/bin/bash", str(root / "scripts/build_dmg.sh")],
        cwd=root.parent,
        env={
            **os.environ,
            "CI": "true",
            "PATH": f"{root / 'tools'}:{os.defpath}",
            "PACKAGING_TOOL_LOG": str(root / "tool.log"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_dmg_includes_current_usage_document_and_env_template(packaging_workspace):
    root = packaging_workspace
    result = run_packaging(root)
    assert result.returncode == 0, result.stderr
    for name in ("README.md", ".env.example"):
        assert (root / "dist/dmg-content" / name).read_bytes() == (root / name).read_bytes()
    assert (root / "dist/dmg-content/Applications").readlink() == Path("/Applications")
    assert "README.md" in (root / "dist/dmg-content/DEVELOPMENT-NOTICE.txt").read_text()


@pytest.mark.parametrize("missing", ["README.md", ".env.example"])
def test_missing_packaging_document_fails_before_build(packaging_workspace, missing):
    root = packaging_workspace
    (root / missing).unlink()
    result = run_packaging(root)
    assert result.returncode != 0
    assert f"缺少打包必需文件：{missing}" in result.stderr
    assert "python" not in (root / "tool.log").read_text().splitlines()
    assert not (root / "dist").exists()
