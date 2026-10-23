"""Check the generated Pipeline dependencies and merge/release gates.

Source: zetetic-team-subagents single-ci design (2026-10-22), and
.github/workflows/pipeline.yml template 247a56967a66dc056eed9ddbb704e56b2b311f81.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pipeline.yml"
# Source: the generated Pipeline contract and sequential test categories.
TEST_JOBS = ("unit", "regression", "e2e", "environment", "install")
REQUIRED_RESULTS = ("contract", *TEST_JOBS)
EXPECTED_NEEDS = {
    "contract": (),
    "unit": ("contract",),
    "regression": ("contract", "unit"),
    "e2e": ("contract", "regression"),
    "environment": ("contract", "e2e"),
    "install": ("contract", "environment"),
    "review": TEST_JOBS,
    "merge": (*REQUIRED_RESULTS, "review"),
    "tag": REQUIRED_RESULTS,
}
# Source: generated review/merge conditions; tag inherits GitHub success().
_PR_CONDITION = (
    "always() && !cancelled() && github.event_name == 'pull_request' "
    "&& !github.event.pull_request.draft "
    "&& github.event.pull_request.head.repo.full_name == github.repository"
)
EXPECTED_CONDITIONS = {
    "review": _PR_CONDITION + " && github.actor != 'dependabot[bot]'",
    "merge": _PR_CONDITION,
    "tag": "github.event_name == 'push'",
}
_JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def _job_bodies(text: str) -> dict[str, list[str]]:
    """Return job id -> the lines of its block, in file order."""
    bodies: dict[str, list[str]] = {}
    in_jobs = False
    current: str | None = None

    for line in text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        # A new top-level key (indent 0) ends the jobs mapping.
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        job_match = _JOB_RE.match(line)
        if job_match:
            current = job_match.group(1)
            if current in bodies:
                raise ValueError(f"duplicate job '{current}'")
            bodies[current] = []
            continue
        if current is not None:
            bodies[current].append(line)

    return bodies


def _field(body: list[str], name: str) -> str:
    """Read one job-level scalar or indented block; reject duplicate fields."""
    values: list[str] = []
    collecting = False
    for line in body:
        if line.startswith(f"    {name}:"):
            if values:
                raise ValueError(f"duplicate job-level {name}")
            values.append(line.split(":", 1)[1].strip())
            collecting = True
        elif collecting and line.startswith("      "):
            if line.strip() and not line.strip().startswith("#"):
                values.append(line.strip())
        elif line.strip() and not line.strip().startswith("#"):
            collecting = False
    if values and values[0] in ("", ">-", ">", "|", "|-"):
        values.pop(0)
    return " ".join(values)


def _parse_needs(body: list[str]) -> list[str]:
    value = _field(body, "needs")
    if value.startswith("[") and value.endswith("]"):
        return [item.strip() for item in value[1:-1].split(",") if item.strip()]
    if value.startswith("- "):
        return [item.strip() for item in value[2:].split(" - ")]
    return [value] if value else []


def _check_dependencies(bodies: dict[str, list[str]]) -> list[str]:
    failures = []
    for job, body in bodies.items():
        needs = _parse_needs(body)
        if len(needs) != len(set(needs)) or job in needs:
            failures.append(f"{job}.needs has a duplicate or self dependency")
        for dependency in needs:
            if dependency not in bodies:
                failures.append(f"{job}.needs names unknown job '{dependency}'")
        expected = EXPECTED_NEEDS.get(job)
        if expected is not None and set(needs) != set(expected):
            failures.append(
                f"{job} must depend on exactly {', '.join(expected) or 'nothing'}"
            )
    return failures


def check(text: str) -> list[str]:
    """Reject missing, orphaned, or wrongly ordered Pipeline jobs."""
    try:
        bodies = _job_bodies(text)
        failures = _check_dependencies(bodies)
    except ValueError as error:
        return [str(error)]
    failures.extend(
        f"missing Pipeline job '{job}'" for job in EXPECTED_NEEDS if job not in bodies
    )
    failures.extend(
        f"job '{job}' is outside the Pipeline gates"
        for job in bodies
        if job not in EXPECTED_NEEDS
    )
    return failures


def _check_merge_results(body: list[str]) -> list[str]:
    """Pin the generated fail-closed result guard, not the removed ALLOWED_SKIPS."""
    guard: list[str] = []
    collecting = False
    for line in body:
        if line.startswith("      - "):
            collecting = line == "      - name: Require completed tests and review"
        elif collecting:
            guard.append(line)
    if not guard:
        return ["merge must retain its completed tests and review guard step"]
    # The generated step fields have four more spaces than job fields.
    fields = [line[4:] for line in guard]
    failures = []
    if _field(fields, "if"):
        failures.append("merge result guard must run unconditionally")
    if _field(fields, "continue-on-error") not in ("", "false"):
        failures.append("merge result guard must not ignore failures")
    text = "\n".join(guard)
    compact = " ".join(text.split())
    for job in (*REQUIRED_RESULTS, "review"):
        binding = f"{job.upper()}: ${{{{ needs.{job}.result }}}}"
        if binding not in text:
            failures.append(f"merge must bind the live {job} result")
    pairs = " ".join(f'"{job}=${job.upper()}"' for job in REQUIRED_RESULTS)
    # Source: merge's Require completed tests and review step in the template.
    clauses = (
        f"for pair in {pairs}; do",
        'case "$pair" in *=success) ;; *)',
        "blocked=1 ;; esac done",
        "case \"$REVIEW:$ACTOR\" in success:*|'skipped:dependabot[bot]') ;; *)",
        'blocked=1 ;; esac if [ -n "$blocked" ]; then',
        "exit 1 fi",
    )
    if any(clause not in compact for clause in clauses):
        failures.append("merge must retain its fail-closed test/review result policy")
    return failures


def check_runtime_contract(text: str) -> list[str]:
    """Keep tests unconditional and merge/release gates on their intended events."""
    try:
        bodies = _job_bodies(text)
        failures = []
        for job in EXPECTED_NEEDS:
            condition = _field(bodies.get(job, []), "if")
            if condition != EXPECTED_CONDITIONS.get(job, ""):
                failures.append(
                    f"{job} has an unsafe job-level condition: {condition!r}"
                )
            if job in REQUIRED_RESULTS and _field(
                bodies.get(job, []), "continue-on-error"
            ) not in ("", "false"):
                failures.append(f"{job} must not ignore failures")
        return failures + _check_merge_results(bodies.get("merge", []))
    except ValueError as error:
        return [str(error)]


def main() -> int:
    if not CI_WORKFLOW.exists():
        print(f"FAIL: {CI_WORKFLOW} not found", file=sys.stderr)
        return 1
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    failures = check(text) + check_runtime_contract(text)
    if failures:
        print("Pipeline gate completeness: FAIL", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("Pipeline gate completeness: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
