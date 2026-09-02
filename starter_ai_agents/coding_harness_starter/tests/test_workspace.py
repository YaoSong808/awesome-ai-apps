from __future__ import annotations

import sys
from pathlib import Path

import pytest

from main import approved_by_human
from models import FileChange
from workspace import ALLOWED_TEST_COMMAND, Workspace, WorkspaceViolation


def change(path: str, content: str = "updated\n") -> FileChange:
    return FileChange(path=path, new_content=content, reason="test change")


def test_lists_and_reads_only_workspace_files(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    workspace = Workspace(tmp_path)

    assert workspace.list_files() == ["module.py"]
    assert workspace.read_file("module.py") == "value = 1\n"


def test_rejects_path_traversal(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceViolation, match="escapes"):
        workspace.read_file("../secret.txt")


def test_rejects_symlink_that_escapes_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace_root / "link.txt").symlink_to(outside)

    with pytest.raises(WorkspaceViolation, match="escapes"):
        Workspace(workspace_root).read_file("link.txt")


def test_does_not_expose_environment_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
    (tmp_path / "config.txt").symlink_to(tmp_path / ".env")
    workspace = Workspace(tmp_path)

    assert workspace.list_files() == []
    with pytest.raises(WorkspaceViolation, match="not exposed"):
        workspace.read_file(".env")
    with pytest.raises(WorkspaceViolation, match="not exposed"):
        workspace.read_file("config.txt")


def test_requires_approval_before_writing(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("original\n", encoding="utf-8")
    workspace = Workspace(tmp_path)

    with pytest.raises(PermissionError, match="explicit human approval"):
        workspace.apply_changes([change("module.py")], approved=False)

    assert target.read_text(encoding="utf-8") == "original\n"


def test_applies_an_approved_change(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("original\n", encoding="utf-8")
    workspace = Workspace(tmp_path)

    changed = workspace.apply_changes([change("module.py")], approved=True)

    assert changed == ["module.py"]
    assert target.read_text(encoding="utf-8") == "updated\n"


def test_runs_only_the_predefined_pytest_command(tmp_path: Path) -> None:
    (tmp_path / "test_example.py").write_text(
        "def test_example():\n    assert 2 + 2 == 4\n", encoding="utf-8"
    )

    result = Workspace(tmp_path).run_tests()

    assert result.command == list(ALLOWED_TEST_COMMAND)
    assert result.command == [sys.executable, "-m", "pytest", "-q"]
    assert result.passed


def test_approved_fixture_fix_turns_red_tests_green(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text(
        "def add(left, right):\n    return left - right\n", encoding="utf-8"
    )
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path)

    assert not workspace.run_tests().passed
    workspace.apply_changes(
        [
            change(
                "calculator.py",
                "def add(left, right):\n    return left + right\n",
            )
        ],
        approved=True,
    )

    assert workspace.run_tests().passed


@pytest.mark.parametrize("answer", ["y", "Y", "yes", " YES "])
def test_accepts_explicit_approval(answer: str) -> None:
    assert approved_by_human(answer)


@pytest.mark.parametrize("answer", ["", "n", "no", "sure", "ok"])
def test_rejects_ambiguous_approval(answer: str) -> None:
    assert not approved_by_human(answer)
