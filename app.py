"""Serve the local Multi-Agent SE workbench and its same-origin JSON API."""

from __future__ import annotations

import argparse
import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from workflow.engine import WorkflowEngine, WorkflowError
from workflow.provider import DeepSeekProvider, DemoProvider
from workflow.store import RunStore

ROOT = Path(__file__).resolve().parent
MAX_BODY = 24_576
LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, engine, provider_name, model_tiers):
        self.engine = engine
        self.info = {"provider": provider_name, "model_tiers": model_tiers}
        super().__init__(("127.0.0.1", port), WorkbenchHandler)


class WorkbenchHandler(BaseHTTPRequestHandler):
    server: WorkbenchServer

    def log_message(self, _format, *_args):
        # The CLI logs startup and unexpected errors, never request payloads.
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        try:
            self._verify_local_request(method)
            url = urlsplit(self.path)
            if url.path == "/api" or url.path.startswith("/api/"):
                status, response = self._route(method, url.path, parse_qs(url.query))
                self._json(status, response)
            elif method == "GET":
                self._static(url.path)
            else:
                raise ApiError(405, "method_not_allowed", "该路径只支持读取。")
        except (ApiError, WorkflowError) as error:
            self._json(error.status, {"error": {"code": error.code, "message": str(error)}})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            LOGGER.error("Unexpected request failure: %s", type(error).__name__)
            self._json(500, {"error": {"code": "internal_error", "message": "处理失败，请重试或查看终端。"}})

    def _verify_local_request(self, method):
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed_hosts.update({"127.0.0.1", "localhost"})
        host = self.headers.get("Host", "").lower()
        if host not in allowed_hosts:
            raise ApiError(403, "local_host_required", "请从本机地址打开工作台。")
        origin = self.headers.get("Origin")
        if method == "POST" and origin and origin != f"http://{host}":
            raise ApiError(403, "origin_rejected", "此操作需要从工作台页面发起。")

    def _route(self, method, path, query):
        engine = self.server.engine
        if method == "GET" and path == "/api/health":
            return 200, {"status": "ok", **self.server.info}
        if method == "GET" and path == "/api/info":
            return 200, self.server.info
        if path == "/api/runs":
            if method == "GET":
                return 200, {"runs": engine.list_runs()}
            data = self._read_body()
            idea = self._text(data, "idea", 5000, required=True)
            title = self._text(data, "title", 120) or idea[:32]
            return 201, engine.create_run(title=title, idea=idea)

        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[:2] != ["api", "runs"]:
            raise ApiError(404, "not_found", "找不到该接口。")
        run_id = parts[2]
        if not run_id or len(run_id) > 100:
            raise ApiError(404, "not_found", "找不到该运行。")
        if method == "GET" and len(parts) == 3:
            return 200, engine.get_run(run_id)
        if method == "GET" and len(parts) == 4 and parts[3] in {"history", "replay"}:
            return 200, engine.get_run(run_id, at=self._query_int(query, "seq", minimum=1))
        if method == "GET" and len(parts) == 4 and parts[3] == "workflow":
            sequence = self._query_int(query, "seq", minimum=1) if "seq" in query else None
            snapshot = engine.get_run(run_id, at=sequence)
            tasks = snapshot.get("work_plan", [])
            if not tasks:
                raise ApiError(409, "plan_not_ready", "该版本尚未生成任务编排。")
            return 200, {
                "format_version": 1,
                "run_id": run_id,
                "run_version": snapshot["version"],
                "title": snapshot["title"],
                "tasks": tasks,
                "phases": snapshot["phases"],
            }
        if method == "GET" and len(parts) == 4 and parts[3] == "events":
            after = self._query_int(query, "after", minimum=0, default=0)
            snapshot = engine.get_run(run_id)
            return 200, {
                "version": snapshot["version"],
                "events": [event for event in snapshot["events"] if event["seq"] > after],
            }
        if method == "POST" and len(parts) == 4 and parts[3] == "actions":
            data = self._read_body()
            version = data.get("expected_version")
            if type(version) is not int or version < 0:
                raise ApiError(400, "invalid_version", "请提供当前版本号。")
            return 200, engine.command(
                run_id,
                action=self._text(data, "action", 40, required=True),
                expected_version=version,
                note=self._text(data, "note", 3000),
                objection_id=self._text(data, "objection_id", 100) or None,
                command_id=self._text(data, "command_id", 128) or None,
            )
        raise ApiError(404, "not_found", "找不到该接口。")

    def _read_body(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "json_required", "请使用 JSON 提交请求。")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as error:
            raise ApiError(400, "invalid_length", "请求长度无效。") from error
        if length < 1 or length > MAX_BODY:
            raise ApiError(413, "body_size", "请求内容为空或超出长度限制。")
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as error:
            raise ApiError(400, "invalid_json", "请求内容不是有效的 JSON。") from error
        if not isinstance(data, dict):
            raise ApiError(400, "object_required", "请求需要 JSON 对象。")
        return data

    @staticmethod
    def _text(data, key, limit, required=False):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            raise ApiError(400, "invalid_field", f"字段 {key} 格式无效或超出长度限制。")
        value = value.strip()
        if required and not value:
            raise ApiError(400, "missing_field", f"请填写 {key}。")
        return value

    @staticmethod
    def _query_int(query, key, minimum, default=None):
        values = query.get(key)
        if values is None and default is not None:
            return default
        try:
            if values is None or len(values) != 1:
                raise ValueError
            result = int(values[0])
            if not minimum <= result <= 2**31 - 1:
                raise ValueError
        except ValueError as error:
            raise ApiError(400, "invalid_query", f"参数 {key} 需要有效的整数。") from error
        return result

    def _static(self, path):
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/mark.svg": ("mark.svg", "image/svg+xml"),
        }
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if path not in files:
            raise ApiError(404, "not_found", "找不到该页面。")
        name, content_type = files[path]
        try:
            content = (ROOT / "frontend" / name).read_bytes()
        except FileNotFoundError as error:
            raise ApiError(404, "asset_missing", "界面文件不存在。") from error
        self._send(200, content, content_type)

    def _json(self, status, response):
        content = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, content, "application/json; charset=utf-8")

    def _send(self, status, content, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        if content:
            self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description="启动第11组多智能体软件工程工作台。")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "workflow.sqlite3")
    parser.add_argument("--provider", choices=("demo", "deepseek"), default="demo")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    with_key = args.provider == "deepseek"
    if with_key and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        parser.error("DeepSeek 模式需要在当前进程环境中配置 DEEPSEEK_API_KEY。")
    provider = DeepSeekProvider() if with_key else DemoProvider()
    model_tiers = getattr(provider, "model_tiers", {})
    args.db.parent.mkdir(parents=True, exist_ok=True)
    engine = WorkflowEngine(RunStore(args.db), provider)
    with WorkbenchServer(args.port, engine, args.provider, model_tiers) as server:
        print(f"工作台 http://127.0.0.1:{server.server_port}  |  模型模式 {args.provider}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n工作台已关闭。")


if __name__ == "__main__":
    main()
