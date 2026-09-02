"""Constrained filesystem and test runner for the coding harness."""

from __future__ import annotations

import difflib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from models import FileChange, TestResult

IGNORED_PARTS = {".env", ".git", ".pytest_cache", ".venv", "__pycache__"}
MAX_FILE_SIZE = 50_000
ALLOWED_TEST_COMMAND = (sys.executable, "-m", "pytest", "-q")


class WorkspaceViolation(ValueError):
    """Raised when an operation would escape the configured workspace."""


class Workspace:
    """Expose only bounded repository operations to the harness."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise WorkspaceViolation(f"Workspace is not a directory: {self.root}")

    def _resolve(self, relative_path: str) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            raise WorkspaceViolation("Absolute paths are not allowed.")

        resolved = (self.root / path).resolve(strict=False)
        try:
            resolved_relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceViolation(
                f"Path escapes the configured workspace: {relative_path!r}"
            ) from exc
        if any(
            part in IGNORED_PARTS for part in (*path.parts, *resolved_relative.parts)
        ):
            raise WorkspaceViolation(
                f"Path is not exposed to the agent: {relative_path!r}"
            )
        return resolved

    def list_files(self) -> list[str]:
        """Return readable files relative to the workspace root."""
        files = []
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            if path.is_file():
                try:
                    self._resolve(relative.as_posix())
                except WorkspaceViolation:
                    continue
                files.append(relative.as_posix())
        return files

    def read_file(self, relative_path: str) -> str:
        """Read one UTF-8 file after enforcing the workspace boundary."""
        path = self._resolve(relative_path)
        if not path.is_file():
            raise WorkspaceViolation(f"File does not exist: {relative_path!r}")
        if path.stat().st_size > MAX_FILE_SIZE:
            raise WorkspaceViolation(
                f"File exceeds the {MAX_FILE_SIZE}-byte read limit: {relative_path!r}"
            )
        return path.read_text(encoding="utf-8")

    def preview_diff(self, change: FileChange) -> str:
        """Build a unified diff without mutating the workspace."""
        path = self._resolve(change.path)
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        return "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                change.new_content.splitlines(keepends=True),
                fromfile=f"a/{change.path}",
                tofile=f"b/{change.path}",
            )
        )

    def apply_changes(self, changes: list[FileChange], *, approved: bool) -> list[str]:
        """Apply changes only after an explicit approval decision."""
        if not approved:
            raise PermissionError("File writes require explicit human approval.")

        resolved: list[tuple[FileChange, Path]] = []
        seen: set[Path] = set()
        for change in changes:
            path = self._resolve(change.path)
            if len(change.new_content.encode("utf-8")) > MAX_FILE_SIZE:
                raise WorkspaceViolation(
                    f"Proposed content exceeds {MAX_FILE_SIZE} bytes: {change.path!r}"
                )
            if path in seen:
                raise WorkspaceViolation(f"Duplicate change for {change.path!r}")
            seen.add(path)
            resolved.append((change, path))

        changed_files = []
        for change, path in resolved:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(change.new_content, encoding="utf-8")
            changed_files.append(change.path)
        return changed_files

    def run_tests(self, *, timeout_seconds: int = 30) -> TestResult:
        """Run the single predefined pytest command without a shell."""
        with tempfile.TemporaryDirectory(prefix="coding-harness-pycache-") as cache:
            env = os.environ.copy()
            env["PYTHONPYCACHEPREFIX"] = cache
            completed = subprocess.run(
                list(ALLOWED_TEST_COMMAND),
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
                env=env,
            )
        return TestResult(
            command=list(ALLOWED_TEST_COMMAND),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
