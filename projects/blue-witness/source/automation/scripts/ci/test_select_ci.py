"""Regression cases for paid job selection; no runner or network required."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("select_ci", Path(__file__).with_name("select-ci.py"))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class SelectionTests(unittest.TestCase):
    def check(self, matches, event="pull_request", ref="refs/pull/80/merge", *,
              components=(), server=False, contracts=False, aruco=False):
        result = policy.select_checks(matches, event, ref)
        self.assertEqual(json.loads(result["components"]), list(components))
        for key, expected in (("server", server), ("contracts", contracts), ("aruco", aruco)):
            self.assertEqual(result[key], str(expected).lower(), key)

    def test_docs_and_deployment_scripts_need_no_build(self):
        for event, ref in (("push", "refs/heads/main"), ("pull_request", "refs/pull/80/merge")):
            self.check([], event, ref)
            self.check(["code"], event, ref)

    def test_each_pr_component_is_built_once(self):
        for component in policy.PI_COMPONENTS:
            self.check(["code", component], components=[component])

    def test_multiple_components_share_one_job(self):
        self.check(["filter", "sensor", "filter"], components=["sensor", "filter"])

    def test_main_pi_change_keeps_full_deployment_bundle(self):
        for component in policy.PI_COMPONENTS:
            self.check([component], "push", "refs/heads/main", components=policy.PI_COMPONENTS)

    def test_server_changes_do_not_build_pi_or_aruco(self):
        for event, ref in (("push", "refs/heads/main"), ("pull_request", "refs/pull/80/merge")):
            self.check(["server"], event, ref, server=True)
            self.check(["server", "contracts"], event, ref, server=True, contracts=True)

    def test_aruco_change_runs_only_aruco(self):
        self.check(["aruco"], "push", "refs/heads/main", aruco=True)

    def test_manual_build_remains_complete(self):
        self.check([], "workflow_dispatch", "refs/heads/main", components=policy.PI_COMPONENTS,
                   server=True, contracts=True, aruco=True)


if __name__ == "__main__":
    unittest.main()
