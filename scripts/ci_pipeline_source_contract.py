"""Static source-commit invariants of the shared Pipeline template.

Source: zetetic-team-subagents PR189 templates/ci/pipeline.yml, protected
workflow_run resolver and Record tested commit step (2026-10-23).
"""

from __future__ import annotations

import re

# Source: contract checkout and downstream checkout refs in the shared template.
SOURCE_REF = (
    "${{ github.event_name == 'workflow_run' && "
    "needs.resolve.outputs.tested_sha || github.sha }}"
)
TESTED_REF = "${{ needs.contract.outputs.tested_sha }}"


def _steps(body: list[str]) -> list[list[str]]:
    steps: list[list[str]] = []
    for line in body:
        if line.startswith("      - "):
            steps.append(["    " + line[8:]])
        elif steps and line.startswith("        "):
            steps[-1].append(line[4:])
    return steps


def _required_step(body, field, identifier: str) -> list[str]:
    matches = [step for step in _steps(body) if field(step, "id") == identifier]
    return matches[0] if len(matches) == 1 else []


def _unconditional(step, field) -> bool:
    return (
        bool(step)
        and not field(step, "if")
        and field(step, "continue-on-error") in ("", "false")
    )


def _check_checkout(body, field, expected: str, job: str) -> list[str]:
    checkouts = [
        step
        for step in _steps(body)
        if field(step, "uses").startswith("actions/checkout@")
    ]
    if len(checkouts) != 1:
        return [f"{job} must have exactly one source checkout"]
    step = checkouts[0]
    refs = [line.strip() for line in step if line.startswith("      ref:")]
    failures = []
    if refs != [f"ref: {expected}"]:
        failures.append(f"{job} must check out the tested source SHA")
    condition = "steps.guard.outputs.has_deploy_key == 'true'" if job == "tag" else ""
    if field(step, "if") != condition or field(step, "continue-on-error") not in (
        "",
        "false",
    ):
        failures.append(f"{job} checkout must retain its execution and failure policy")
    return failures


def _check_resolver(body, field) -> list[str]:
    failures = []
    outputs = field(body, "outputs")
    for name in ("tested_sha", "run_tests"):
        if f"{name}: ${{{{ steps.resolve.outputs.{name} }}}}" not in outputs:
            failures.append(f"resolve must bind its actual {name} output")
    if not _unconditional(_required_step(body, field, "resolve"), field):
        failures.append(
            "resolve must execute its protected merge step without ignoring failure"
        )
    return failures


def _check_record(body, field) -> list[str]:
    step = _required_step(body, field, "source")
    failures = []
    if not _unconditional(step, field):
        failures.append("contract must unconditionally verify the checked-out SHA")
    if field(step, "env") != f"EXPECTED_SHA: {SOURCE_REF}":
        failures.append("contract must verify the resolved source SHA")
    expected_run = (
        'actual="$(git rev-parse HEAD)" test "$actual" = "$EXPECTED_SHA" '
        'echo "tested_sha=$actual" >> "$GITHUB_OUTPUT"'
    )
    if field(step, "run") != expected_run:
        failures.append("contract must record the verified actual checkout SHA")
    if "tested_sha: ${{ steps.source.outputs.tested_sha }}" not in field(
        body, "outputs"
    ):
        failures.append("contract must expose the verified checkout SHA")
    return failures


def check_sources(bodies, field, test_jobs, text: str) -> list[str]:
    """Check producer output bindings, verification, and every consumer checkout."""
    failures = _check_resolver(bodies.get("resolve", []), field)
    triggers = text.split("\njobs:", 1)[0]
    if len(re.findall(r"(?m)^  workflow_call:\s*$", triggers)) != 1 or re.search(
        r"(?m)^  workflow_run:", triggers
    ):
        failures.append("Pipeline must be reusable and cannot listen to itself")
    failures += _check_record(bodies.get("contract", []), field)
    for job in ("contract", *test_jobs, "tag"):
        expected = SOURCE_REF if job == "contract" else TESTED_REF
        failures += _check_checkout(bodies.get(job, []), field, expected, job)
    return failures
