"""Command-line entry point for the minimal coding harness."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from agent import build_agent
from models import RunSummary
from tools import AgentDependencies
from workspace import Workspace

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "fixture_repo"
DEFAULT_TASK = "Fix the bug in calculator.add so the repository tests pass."


def parse_args() -> argparse.Namespace:
    """Parse the coding task and optional workspace path."""
    parser = argparse.ArgumentParser(description="Run a human-gated coding agent.")
    parser.add_argument("task", nargs="?", default=DEFAULT_TASK)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help="Repository directory the agent may inspect and edit.",
    )
    return parser.parse_args()


def approved_by_human(answer: str) -> bool:
    """Accept only an unambiguous affirmative approval."""
    return answer.strip().lower() in {"y", "yes"}


def main() -> int:
    """Run inspect, propose, approve, apply, test, and summarize."""
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    workspace = Workspace(args.workspace)
    agent = build_agent()

    result = asyncio.run(agent.run(args.task, deps=AgentDependencies(workspace)))
    proposal = result.output

    print("\n=== STRUCTURED PROPOSAL ===")
    print(proposal.model_dump_json(indent=2))
    print("\n=== PATCH PREVIEW ===")
    for change in proposal.changes:
        print(f"\n# {change.path}: {change.reason}")
        print(workspace.preview_diff(change) or "(no changes)")

    answer = input("\nApply these changes and run the allowlisted tests? [y/N] ")
    if not approved_by_human(answer):
        summary = RunSummary(status="rejected")
        print("\n=== RUN SUMMARY ===")
        print(summary.model_dump_json(indent=2))
        return 0

    changed_files = workspace.apply_changes(proposal.changes, approved=True)
    test_result = workspace.run_tests()
    summary = RunSummary(
        status="passed" if test_result.passed else "tests_failed",
        changed_files=changed_files,
        tests=test_result,
    )
    print("\n=== RUN SUMMARY ===")
    print(json.dumps(summary.model_dump(), indent=2))
    return 0 if test_result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
