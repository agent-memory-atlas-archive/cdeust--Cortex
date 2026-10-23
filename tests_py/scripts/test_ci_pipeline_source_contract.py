"""Mutations of the protected merge resolver and tested commit provenance."""

from __future__ import annotations

import unittest

from test_check_ci_gate_complete import change_job, gate


class PipelineSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = gate.CI_WORKFLOW.read_text(encoding="utf-8")

    def reject(self, job: str, before: str, after: str) -> None:
        changed = change_job(
            self.workflow, job, lambda block: block.replace(before, after)
        )
        self.assertTrue(gate.check_runtime_contract(changed))

    def test_pipeline_is_reusable_without_listening_to_itself(self) -> None:
        for replacement in ("", "  workflow_run:\n"):
            with self.subTest(replacement=replacement):
                changed = self.workflow.replace("  workflow_call:\n", replacement)
                self.assertNotEqual(changed, self.workflow)
                self.assertTrue(gate.check_runtime_contract(changed))

    def test_categories_and_tag_require_each_successful_dependency(self) -> None:
        for job in (*gate.TEST_JOBS, "tag"):
            for dependency in gate.EXPECTED_NEEDS[job]:
                for replacement in ("true", f"needs.{dependency}.result != 'failure'"):
                    with self.subTest(
                        job=job, dependency=dependency, value=replacement
                    ):
                        self.reject(
                            job, f"needs.{dependency}.result == 'success'", replacement
                        )

    def test_categories_and_tag_must_handle_skipped_resolve_and_cancellation(
        self,
    ) -> None:
        for job in (*gate.TEST_JOBS, "tag"):
            for replacement in ("success()", "always()", "true"):
                with self.subTest(job=job, value=replacement):
                    self.reject(job, "!cancelled()", replacement)

    def test_resolver_requires_successful_pr_source(self) -> None:
        for before, after in (
            ("github.event_name == 'workflow_run'", "true"),
            ("github.event.workflow_run.event == 'pull_request'", "true"),
            ("github.event.workflow_run.conclusion == 'success'", "true"),
        ):
            with self.subTest(before=before):
                self.reject("resolve", before, after)

    def test_resolver_cannot_ignore_failure(self) -> None:
        self.reject(
            "resolve", "  resolve:\n", "  resolve:\n    continue-on-error: true\n"
        )

    def test_contract_requires_successful_resolver_and_permission_to_test(self) -> None:
        for before, after in (
            ("needs.resolve.result == 'success'", "true"),
            ("needs.resolve.outputs.run_tests == 'true'", "true"),
            ("always() && ", ""),
            ("!cancelled() && ", ""),
        ):
            with self.subTest(before=before):
                self.reject("contract", before, after)

    def test_resolver_outputs_must_come_from_its_step(self) -> None:
        for output in ("tested_sha", "run_tests"):
            with self.subTest(output=output):
                self.reject("resolve", f"steps.resolve.outputs.{output}", "github.sha")

    def test_resolver_step_cannot_be_skipped_or_tolerate_failure(self) -> None:
        for field in ("if: false", "continue-on-error: true"):
            with self.subTest(field=field):
                self.reject(
                    "resolve",
                    "        id: resolve\n",
                    f"        id: resolve\n        {field}\n",
                )

    def test_contract_checkout_requires_resolved_commit(self) -> None:
        self.reject("contract", "needs.resolve.outputs.tested_sha", "github.sha")

    def test_every_test_and_tag_checkout_requires_contract_sha(self) -> None:
        for job in (*gate.TEST_JOBS, "tag"):
            with self.subTest(job=job):
                self.reject(job, "needs.contract.outputs.tested_sha", "github.sha")

    def test_tested_sha_output_requires_verified_checkout(self) -> None:
        for before, after in (
            ("steps.source.outputs.tested_sha", "github.sha"),
            ('test "$actual" = "$EXPECTED_SHA"', "true"),
            ('echo "tested_sha=$actual"', 'echo "tested_sha=$EXPECTED_SHA"'),
            ('actual="$(git rev-parse HEAD)"', 'actual="$EXPECTED_SHA"'),
            ("        id: source\n", "        id: source\n        if: false\n"),
            (
                "        id: source\n",
                "        id: source\n        continue-on-error: true\n",
            ),
        ):
            with self.subTest(before=before):
                self.reject("contract", before, after)

    def test_checkout_cannot_be_missing_skipped_or_forged(self) -> None:
        for job in ("contract", *gate.TEST_JOBS):
            for before, after in (
                ("      - uses: actions/checkout@", "      - uses: other/checkout@"),
                ("        with:\n", "        if: false\n        with:\n"),
                ("        with:\n", "        continue-on-error: true\n        with:\n"),
            ):
                with self.subTest(job=job, before=before):
                    self.reject(job, before, after)


if __name__ == "__main__":
    unittest.main()
