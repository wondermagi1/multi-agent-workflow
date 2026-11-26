import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from app import WorkbenchServer
from workflow.engine import WorkflowEngine
from workflow.provider import DemoProvider
from workflow.store import RunStore


class WorkbenchHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        engine = WorkflowEngine(RunStore(Path(self.directory.name) / "test.sqlite"), DemoProvider())
        self.server = WorkbenchServer(0, engine, "demo", {"economy": "demo"})
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def request(self, method, path, payload=None, headers=None):
        request_headers = {} if headers is None else headers.copy()
        body = None
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            if "application/json" in response.headers.get("Content-Type", ""):
                data = json.loads(data)
            return response.status, data
        finally:
            connection.close()

    def test_new_run_lists_and_replays_committed_history(self):
        status, before = self.request("POST", "/api/runs", {"title": "演练", "idea": "设计供成员协作的任务分配工具"})
        self.assertEqual(status, 201, before)
        route = "/api/runs/" + before["id"]
        status, index = self.request("GET", "/api/runs")
        self.assertEqual(status, 200)
        self.assertIn(before["id"], [run["id"] for run in index["runs"]])

        status, after = self.request("POST", route + "/actions", {
            "action": "advance", "expected_version": before["version"], "command_id": "test-advance",
        })
        self.assertEqual(status, 200, after)
        self.assertGreater(after["version"], before["version"])
        oldest = before["events"][-1]["seq"]
        status, history = self.request("GET", f"{route}/history?seq={oldest}")
        self.assertEqual(status, 200, history)
        self.assertEqual(history["version"], before["version"])
        status, export = self.request("GET", route + "/workflow")
        self.assertEqual(status, 200, export)
        self.assertEqual(export["run_version"], after["version"])
        self.assertEqual(export["tasks"], after["work_plan"])
        self.assertTrue(all({"depends_on", "role", "difficulty", "tier"} <= task.keys() for task in export["tasks"]))
        status, export = self.request("GET", f"{route}/workflow?seq={oldest}")
        self.assertEqual((status, export["error"]["code"]), (409, "plan_not_ready"))
        status, updates = self.request("GET", f"{route}/events?after={oldest}")
        self.assertEqual(status, 200, updates)
        self.assertTrue(updates["events"])
        self.assertTrue(all(event["seq"] > oldest for event in updates["events"]))

    def test_local_origin_and_invalid_requests(self):
        status, denied = self.request("POST", "/api/runs", {"idea": "目标"}, {"Origin": "https://example.test"})
        self.assertEqual((status, denied["error"]["code"]), (403, "origin_rejected"))
        status, denied = self.request("GET", "/api/health", headers={"Host": "unrelated.test"})
        self.assertEqual((status, denied["error"]["code"]), (403, "local_host_required"))
        status, denied = self.request("POST", "/api/runs", {"idea": "  "})
        self.assertEqual((status, denied["error"]["code"]), (400, "missing_field"))
        status, _ = self.request("GET", "/.env")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
