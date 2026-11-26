import tempfile
import unittest
from pathlib import Path

from workflow.engine import WorkflowEngine, WorkflowError
from workflow.provider import DemoProvider
from workflow.store import RunStore


class WorkflowScenarioTests(unittest.TestCase):
    def test_ideas_converge_to_a_signed_and_replayable_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "workflow.sqlite3"
            engine = WorkflowEngine(RunStore(database), DemoProvider())
            run = engine.create_run("流程验收", "设计符合既定目标和约束的软件工作流")
            seen_human_nodes = []
            for _ in range(30):
                if run["status"] == "completed":
                    break
                if run["status"] == "waiting_human":
                    seen_human_nodes.append(run["node"])
                    action = "approve"
                    if run["node"] == "N15":
                        self.assertNotIn("advance", run["allowed_actions"])
                        self.assertFalse(any(item["kind"] == "baseline" for item in run["artifacts"]))
                        with self.assertRaisesRegex(WorkflowError, "版本已变化"):
                            engine.command(run["id"], action, run["version"] - 1)
                else:
                    action = "advance"
                self.assertIn(action, run["allowed_actions"], run)
                run = engine.command(run["id"], action, run["version"], note="按当前范围推进并记录决定")

            self.assertEqual(run["status"], "completed")
            self.assertEqual(seen_human_nodes, ["N02", "N09", "N15", "DEV-A"])
            self.assertEqual({gate["id"] for gate in run["gates"] if gate["passed"]}, {f"G{i}" for i in range(6)})
            evaluation = next(item["content"] for item in run["artifacts"] if item["kind"] == "evaluation")
            self.assertEqual(len(evaluation["reviews"]), 3)
            baseline = next(item["content"] for item in run["artifacts"] if item["kind"] == "baseline")
            self.assertTrue(baseline["requirements"])
            self.assertTrue(baseline["confirmation"]["note"])

            release = next(event["seq"] for event in run["events"] if event["type"] == "proposals.released")
            restored = WorkflowEngine(RunStore(database), DemoProvider())
            before_release = restored.get_run(run["id"], at=release - 1)
            self.assertEqual(before_release["proposals"], [])
            self.assertEqual(before_release["allowed_actions"], [])
            self.assertEqual(len(restored.get_run(run["id"], at=release)["proposals"]), 4)


if __name__ == "__main__":
    unittest.main()
