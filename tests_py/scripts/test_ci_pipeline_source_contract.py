"""Mutations of the protected merge resolver and tested commit provenance."""

from __future__ import annotations

import unittest
from itertools import product

from tests_py.scripts.test_check_ci_gate_complete import change_job, gate


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
        dependencies = (
            (job, dependency)
            for job in (*gate.TEST_JOBS, "tag")
            for dependency in gate.EXPECTED_NEEDS[job]
        )
        replacements = ("true", "needs.{dependency}.result != 'failure'")
        for (job, dependency), template in product(dependencies, replacements):
            replacement = template.format(dependency=dependency)
            with self.subTest(job=job, dependency=dependency, value=replacement):
                self.reject(job, f"needs.{dependency}.result == 'success'", replacement)

    def test_categories_and_tag_must_handle_skipped_resolve_and_cancellation(
        self,
    ) -> None:
        for job, replacement in product(
            (*gate.TEST_JOBS, "tag"), ("success()", "always()", "true")
        ):
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

    def test_every_test_checkout_requires_contract_sha(self) -> None:
        for job in gate.TEST_JOBS:
            with self.subTest(job=job):
                self.reject(job, "needs.contract.outputs.tested_sha", "github.sha")

    def test_privileged_tag_checkout_requires_literal_main(self) -> None:
        for ref in ("${{ needs.contract.outputs.tested_sha }}", "${{ github.sha }}"):
            with self.subTest(ref=ref):
                self.reject("tag", "ref: main", f"ref: {ref}")

    def test_tag_push_requires_main_branch(self) -> None:
        self.reject("tag", "github.ref == 'refs/heads/main'", "true")

    def test_tag_cannot_replace_explicit_target_with_checkout_head(self) -> None:
        for before, after in (
            (
                "TESTED_SHA: ${{ needs.contract.outputs.tested_sha }}",
                "TESTED_SHA: main",
            ),
            ('sha = os.environ["TESTED_SHA"]', 'sha = git("rev-parse", "HEAD").stdout'),
            ("validate(tags, sha)", 'validate(tags, "HEAD")'),
            (
                '"--",\n                          tag,\n                          sha,',
                '"--",\n                          tag,\n'
                '                          "HEAD",',
            ),
        ):
            with self.subTest(before=before):
                self.reject("tag", before, after)

    def test_tag_requires_commit_and_trusted_main_ancestry(self) -> None:
        for before, after in (
            ('"cat-file", "-t", sha', '"cat-file", "-t", "HEAD"'),
            ('"refs/remotes/origin/main^{commit}"', '"HEAD^{commit}"'),
            ('if git("rev-parse", "HEAD").stdout.strip() != trusted:', "if False:"),
            ('git("merge-base", "--is-ancestor", sha, trusted)', "pass"),
        ):
            with self.subTest(before=before):
                self.reject("tag", before, after)

    def test_resolver_must_confirm_post_merge_identity_and_ancestry(self) -> None:
        for before, after in (
            ('pr = api.request("GET", pr_path)', "pr = original_pr"),
            ('_pr_identity(pr, repository, original_pr["head"]["sha"])', "pass"),
            ('pr.get("merge_commit_sha") == sha', "True"),
            ("compare/main...{full_sha(sha)}", "compare/main...main"),
            ('comparison.get("status") in ("behind", "identical")', "True"),
            ('comparison.get("merge_base_commit", {}).get("sha") == sha', "True"),
            ("_confirm_main_merge(pr_path, pr, sha, api)", "pass"),
        ):
            with self.subTest(before=before):
                self.reject("resolve", before, after)

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
        mutations = (
            ("      - uses: actions/checkout@", "      - uses: other/checkout@"),
            ("        with:\n", "        if: false\n        with:\n"),
            ("        with:\n", "        continue-on-error: true\n        with:\n"),
        )
        for job, (before, after) in product(("contract", *gate.TEST_JOBS), mutations):
            with self.subTest(job=job, before=before):
                self.reject(job, before, after)


if __name__ == "__main__":
    unittest.main()
