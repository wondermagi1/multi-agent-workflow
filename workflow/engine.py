"""Versioned orchestration for the local multi-agent workbench."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import math
from threading import Lock
from uuid import uuid4

from .store import VersionConflict, utcnow


PHASES = (
    ("S0", "想法与定题"), ("S1", "多体讨论"), ("S2", "建设方案"),
    ("S3", "多方评价"), ("S4", "需求规格"), ("S5", "需求确认"), ("DEV", "实现与交付"),
)
SEATS = ("product", "technology", "evidence", "challenger")
ROLE_LABELS = {
    "product": "产品与用户", "technology": "技术与可行性", "evidence": "证据与资源",
    "challenger": "独立质疑者", "recorder": "记录与整合", "user": "用户与验收",
}
EVALUATORS = (
    ("product", "价值、范围和资源分配", "balanced"),
    ("user", "真实使用场景、需求覆盖和可验收性", "balanced"),
    ("technology", "技术可行性、依赖约束和实施风险", "strong"),
)
EVALUATION_DIMENSIONS = ("价值", "可行性", "资源", "风险", "可验证性")
NODE_LABELS = {
    "N01": "整理议题与生成任务", "N02": "定题确认", "N03": "冻结简报并派单",
    "N04": "四路独立提案", "N05": "归类与汇总", "N06": "结构化审阅与表态",
    "N07": "反例与失败情境", "N08": "分歧分流", "N09": "人工取舍",
    "N10": "组合修订", "N11": "讨论门禁", "N12": "建设方案", "N13": "多方评价",
    "N14": "需求规格", "N15": "基线签收", "DEV-C": "实现计划", "DEV-T": "模拟测试",
    "DEV-R": "模拟评审", "DEV-A": "交付验收",
}
MAX_ROUNDS = 5
SUPPORT_THRESHOLD = 0.8
TERMINAL = {"completed", "cancelled"}


class WorkflowError(Exception):
    def __init__(self, message, status=400, code="invalid_action"):
        super().__init__(message)
        self.status = status
        self.code = code


class InvalidOutput(Exception):
    """A provider returned unusable structured data."""


class StepFailure(Exception):
    """One or more model attempts could not complete a workflow step."""


def required_text(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InvalidOutput(f"缺少有效字段 {name}")
    return value.strip()


def text_items(value, name, minimum=0, maximum=20):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise InvalidOutput(f"字段 {name} 的条目数无效")
    return [required_text(item, name) for item in value]


def numbered(prefix, items):
    return [{"id": f"{prefix}-{index}", **item} for index, item in enumerate(items, 1)]


def model_usage(value):
    value = value if isinstance(value, dict) else {}

    def integer(key, alternate):
        raw = value.get(key, value.get(alternate))
        return raw if type(raw) is int and raw >= 0 else None

    input_tokens = integer("prompt_tokens", "input_tokens")
    output_tokens = integer("completion_tokens", "output_tokens")
    total = integer("total_tokens", "total_tokens")
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    cost = value.get("estimated_cost_usd")
    return {
        "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total,
        "estimated_cost_usd": cost if type(cost) in (float, int) and math.isfinite(cost) and cost >= 0 else None,
        "source": str(value.get("source", "unknown"))[:40],
        "billed": value.get("billed") if type(value.get("billed")) is bool else None,
    }


class WorkflowEngine:
    def __init__(self, store, provider):
        self.store = store
        self.provider = provider
        self._registry_guard = Lock()
        self._run_locks = {}
        self._recover_incomplete_steps()

    def _recover_incomplete_steps(self):
        for state in self.store.list_runs():
            if state["status"] != "running":
                continue
            for attempt in state["attempts"]:
                if attempt["status"] == "running":
                    attempt.update(status="failed", finished_at=utcnow(), error="运行服务中断，结果未知")
            state.update(status="failed", error="运行服务中断，可重试当前节点。")
            self.store.append(state, "step.interrupted", "检测到未完成的节点")

    def _lock(self, run_id):
        with self._registry_guard:
            return self._run_locks.setdefault(run_id, Lock())

    def list_runs(self):
        keys = ("id", "title", "idea", "version", "stage", "node", "status", "created_at", "updated_at")
        return [{key: state[key] for key in keys} for state in self.store.list_runs()]

    def create_run(self, title, idea):
        try:
            idea = required_text(idea, "议题", 12000)
            title = required_text(title or idea[:40], "标题", 120)
        except InvalidOutput as exc:
            raise WorkflowError(str(exc), code="invalid_run") from None
        state = {
            "id": uuid4().hex[:12], "title": title, "idea": idea, "version": 0, "created_at": utcnow(),
            "stage": "S0", "node": "N01", "status": "ready", "waiting": None, "error": None,
            "round": 1, "max_rounds": MAX_ROUNDS, "support_threshold": SUPPORT_THRESHOLD,
            "artifacts": [], "proposals": [], "claims": [], "stances": [], "objections": [], "gates": [],
            "attempts": [], "decisions": [], "candidates": [], "proposal_release": False,
        }
        self.store.append(state, "run.created", "新建工作流", {"to_node": "N01", "status": "ready"})
        return self.get_run(state["id"])

    def get_run(self, run_id, at=None):
        state = self.store.get(run_id, at=at)
        if state is None:
            raise WorkflowError("没有找到运行或历史版本。", 404, "not_found")
        current = next(index for index, item in enumerate(PHASES) if item[0] == state["stage"])
        phases = []
        for index, (phase, name) in enumerate(PHASES):
            if index < current or state["status"] == "completed":
                status = "completed"
            elif index == current:
                status = state["status"]
            else:
                status = "pending"
            phases.append({"id": phase, "label": name, "status": status})
        state["phases"] = phases
        state["events"] = self.store.events(run_id, through=state["version"])
        state["replay"] = at is not None
        state["allowed_actions"] = self._allowed(state) if at is None else []
        state["usage"] = self._total_usage(state["attempts"])
        state["node_label"] = NODE_LABELS[state["node"]]
        return {key: value for key, value in state.items() if not key.startswith("_")}

    def _total_usage(self, attempts):
        entries = [a["usage"] for a in attempts if "usage" in a]
        totals = {}
        for field in ("input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd"):
            known = [entry[field] for entry in entries if entry[field] is not None]
            totals[field] = (sum(known) if known else None) if attempts else 0
        return {
            **totals, "calls": len(attempts),
            "partial": len(entries) != len(attempts)
            or any(entry["total_tokens"] is None or entry["estimated_cost_usd"] is None for entry in entries),
            "billed_calls": sum(entry["billed"] is True for entry in entries),
        }

    def _allowed(self, state):
        status, node = state["status"], state["node"]
        if status in TERMINAL or status == "running":
            return []
        if status == "paused":
            return ["resume", "cancel"]
        if status == "failed":
            return ["retry", "cancel"]
        if status == "waiting_human":
            return ["approve", "return", "pause", "cancel"]
        if status == "blocked":
            choices = ["return", "pause", "cancel"]
            if node == "N08" and any(self._open_nonvalue(objection) for objection in state["objections"]):
                choices.insert(0, "resolve_objection")
            elif node == "N11" and state["round"] < MAX_ROUNDS:
                choices.insert(0, "retry")
            return choices
        return ["advance", "pause", "cancel"]

    @staticmethod
    def _open_nonvalue(objection):
        return objection["status"] == "open" and objection["type"] != "value"

    def command(self, run_id, action, expected_version, note="", objection_id=None, command_id=None):
        if not isinstance(run_id, str) or not self.store.get(run_id):
            raise WorkflowError("没有找到运行。", 404, "not_found")
        if not isinstance(action, str) or type(expected_version) is not int or expected_version < 1:
            raise WorkflowError("必须提交操作及当前快照版本。", code="invalid_command")
        if not isinstance(note, str) or len(note) > 4000:
            raise WorkflowError("操作说明需为 4000 字以内的文字。", code="invalid_command")
        if command_id is not None and (
            not isinstance(command_id, str) or not command_id or len(command_id) > 120
        ):
            raise WorkflowError("命令标识格式无效。", code="invalid_command")
        with self._lock(run_id):
            state = self.store.get(run_id)
            if command_id is not None:
                receipt = self.store.receipt(run_id, command_id)
                if receipt == action:
                    return self.get_run(run_id)
                if receipt is not None:
                    raise WorkflowError("命令标识已被使用。", 409, "duplicate_command")
            if expected_version != state["version"]:
                raise WorkflowError("快照版本已变化，请刷新后再次操作。", 409, "stale_version")
            if action not in self._allowed(state):
                raise WorkflowError("当前状态不接受该操作。", 409, "transition_denied")
            note = note.strip()
            if action in {"return", "resolve_objection"} or (action == "approve" and state["node"] == "N09"):
                if not note:
                    raise WorkflowError("请填写决定理由或可核验的处理依据。", code="note_required")
            try:
                self._apply(state, action, note, objection_id, command_id)
            except VersionConflict:
                raise WorkflowError("快照版本已变化，请刷新后再次操作。", 409, "stale_version") from None
            return self.get_run(run_id)

    def _apply(self, state, action, note, objection_id, command_id):
        receipt = {"command_id": command_id, "action": action}
        if action in {"advance", "retry"}:
            if state["status"] == "blocked" and state["node"] == "N11":
                self._reopen_discussion(state)
            state.update(status="running", error=None)
            self.store.append(state, "node.started", f"开始{NODE_LABELS[state['node']]}", **receipt)
            try:
                self._run_node(state)
            except (StepFailure, InvalidOutput) as exc:
                state.update(status="failed", error=str(exc))
                self.store.append(state, "node.failed", "当前节点未完成", {"reason": state["error"]})
            return
        if action == "pause":
            state["_paused_status"] = state["status"]
            state["status"] = "paused"
            self.store.append(state, "run.paused", "暂停后续派发", {"note": note}, **receipt)
        elif action == "resume":
            state["status"] = state.pop("_paused_status", "ready")
            self.store.append(state, "run.resumed", "恢复原有状态", {"status": state["status"]}, **receipt)
        elif action == "cancel":
            state.update(status="cancelled", waiting=None)
            self.store.append(state, "run.cancelled", "运行已结束", {"note": note}, **receipt)
        elif action == "approve":
            self._approve(state, note or "确认当前版本与范围", receipt)
        elif action == "return":
            self._return(state, note, receipt)
        elif action == "resolve_objection":
            self._resolve_objection(state, objection_id, note, receipt)

    def _move(self, state, stage, node, status="ready", waiting=None, title=None, details=None, event_type="node.completed", **receipt):
        before = {"stage": state["stage"], "node": state["node"], "status": state["status"]}
        state.update(stage=stage, node=node, status=status, waiting=waiting, error=None)
        self.store.append(
            state, event_type, title or f"进入{NODE_LABELS[node]}",
            {**(details or {}), "before": before, "after": {"stage": stage, "node": node, "status": status}},
            **receipt,
        )

    def _artifact(self, state, kind, title, data, node=None):
        artifact = {
            "id": f"A-{len(state['artifacts']) + 1}", "kind": kind, "title": title, "content": data,
            "node": node or state["node"], "version": state["version"] + 1, "created_at": utcnow(),
        }
        state["artifacts"].append(artifact)
        return artifact

    @staticmethod
    def _latest(state, kind):
        return next((artifact for artifact in reversed(state["artifacts"]) if artifact["kind"] == kind), None)

    @staticmethod
    def _gate(state, gate_id, checks):
        result = {"id": gate_id, "passed": all(passed for _, passed in checks), "node": state["node"],
                  "round": state["round"], "checks": [{"label": key, "passed": passed} for key, passed in checks]}
        state["gates"].append(result)
        return result

    def _invoke(self, state, jobs):
        """Each parallel call receives an input snapshot fixed before any dispatch."""
        attempts = []
        fixed_jobs = [(role, instruction, deepcopy(payload), tier) for role, instruction, payload, tier in jobs]
        for role, instruction, payload, tier in fixed_jobs:
            attempt = {
                "id": f"T-{len(state['attempts']) + 1}", "role": role, "node": state["node"],
                "status": "running", "model": None, "tier": tier, "started_at": utcnow(),
                "task_ids": payload.get("task_ids", []),
            }
            state["attempts"].append(attempt)
            attempts.append(attempt)
            self.store.append(state, "agent.started", f"派发给{ROLE_LABELS[role]}", {
                "attempt_id": attempt["id"], "role": role, "tier": tier, "kind": payload["kind"],
                "task_ids": attempt["task_ids"],
            })
        outputs = [None] * len(jobs)
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            order = {pool.submit(self.provider.generate, *job): index for index, job in enumerate(fixed_jobs)}
            for future in as_completed(order):
                index = order[future]
                attempt = attempts[index]
                role = attempt["role"]
                result = None
                try:
                    result = future.result()
                    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
                        raise InvalidOutput("模型未返回结构化结果")
                    model = required_text(result.get("model"), "模型名称", 120)
                except Exception as exc:
                    # Provider responses may contain confidential input. Only expose stable error categories.
                    reason = "输出格式无效" if isinstance(exc, InvalidOutput) else "模型请求失败，请检查提供方配置与可用性"
                    attempt.update(status="failed", finished_at=utcnow(), error=reason)
                    usage = getattr(exc, "usage", None)
                    if usage is None and isinstance(result, dict):
                        usage = result.get("usage")
                    if usage is not None:
                        attempt["usage"] = model_usage(usage)
                    self.store.append(state, "agent.failed", f"{ROLE_LABELS[role]}调用失败", {
                        "attempt_id": attempt["id"], "role": role, "reason": reason, "usage": attempt.get("usage"),
                    })
                    continue
                attempt.update(status="completed", model=model, finished_at=utcnow(), usage=model_usage(result.get("usage")))
                outputs[index] = result["data"]
                self.store.append(state, "agent.completed", f"{ROLE_LABELS[role]}已返回", {
                    "attempt_id": attempt["id"], "role": role, "model": model, "usage": attempt["usage"],
                    "content_visible": False,
                })
        if any(value is None for value in outputs):
            raise StepFailure("必需的 Agent 返回未收齐，可重试当前节点。")
        return outputs

    def _call(self, state, role, kind, instruction, payload, tier="balanced"):
        return self._invoke(state, [(role, instruction, {"kind": kind, **payload}, tier)])[0]

    def _run_node(self, state):
        handlers = {
            "N01": self._brief, "N03": self._freeze, "N04": self._propose, "N05": self._cluster,
            "N06": self._critique, "N07": self._counterexamples, "N08": self._route, "N10": self._synthesize,
            "N11": self._check_consensus, "N12": self._plan, "N13": self._evaluate, "N14": self._requirements,
            "DEV-C": self._implementation, "DEV-T": self._testing, "DEV-R": self._review,
        }
        handlers[state["node"]](state)

    def _brief(self, state):
        raw = self._call(
            state, "product", "brief", "提炼目标、硬约束与假设。按依赖生成任务图，分配角色并判断难度。",
            {"idea": state["idea"], "title": state["title"], "seats": list(SEATS),
             "clarification": state.get("_brief_feedback", "")},
        )
        brief = {
            "summary": required_text(raw.get("summary"), "summary"),
            "goals": text_items(raw.get("goals"), "goals", 1),
            "constraints": text_items(raw.get("constraints"), "constraints"),
            "assumptions": text_items(raw.get("assumptions"), "assumptions"),
            "tasks": self._task_graph(raw.get("tasks")),
        }
        state["work_plan"] = brief["tasks"]
        artifact = self._artifact(state, "brief", "议题简报与任务拆解", brief)
        self._gate(state, "G0", [("议题和目标明确", True), ("约束、假设和任务依赖已登记", True)])
        self._move(state, "S0", "N02", "waiting_human", waiting={
            "id": "HITL-1", "title": "确认议题简报", "description": "核对目标、硬约束、假设与任务拆解。",
            "artifact_id": artifact["id"],
        })

    @staticmethod
    def _task_graph(raw):
        if not isinstance(raw, list) or not 1 <= len(raw) <= 20 or not all(isinstance(item, dict) for item in raw):
            raise InvalidOutput("任务图需要包含 1 至 20 个结构化任务")
        tiers = {"low": "economy", "medium": "balanced", "high": "strong"}
        tasks = []
        for item in raw:
            role, difficulty = item.get("role"), item.get("difficulty")
            if not isinstance(role, str) or role not in SEATS or not isinstance(difficulty, str) or difficulty not in tiers:
                raise InvalidOutput("任务的角色或难度无效")
            tasks.append({
                "id": required_text(item.get("id"), "task.id", 80),
                "title": required_text(item.get("title"), "task.title", 220),
                "role": role, "difficulty": difficulty, "tier": tiers[difficulty],
                "depends_on": text_items(item.get("depends_on"), "depends_on"),
            })
        dependencies = {item["id"]: set(item["depends_on"]) for item in tasks}
        if len(dependencies) != len(tasks) or any(not refs <= dependencies.keys() for refs in dependencies.values()):
            raise InvalidOutput("任务标识重复或引用了未知依赖")
        done = set()
        while len(done) < len(tasks):
            ready = {key for key, deps in dependencies.items() if key not in done and deps <= done}
            if not ready:
                raise InvalidOutput("任务图存在环形依赖")
            done.update(ready)
        return tasks

    def _freeze(self, state):
        draft = self._latest(state, "brief")
        self._artifact(state, "brief_baseline", "已冻结的议题简报", {**draft["content"], "source_id": draft["id"]})
        self._move(state, "S1", "N04", title="冻结简报，准备四席独立提案", details={"seats": list(SEATS)})

    def _propose(self, state):
        brief = self._latest(state, "brief_baseline")
        tier_order = ("economy", "balanced", "strong")
        jobs = []
        for role in SEATS:
            tasks = [item for item in brief["content"]["tasks"] if item["role"] == role]
            tier = max((item["tier"] for item in tasks), key=tier_order.index, default="balanced")
            payload = {"kind": "proposal", "brief": brief["content"], "brief_version": brief["version"],
                       "task_ids": [item["id"] for item in tasks]}
            jobs.append((role, "独立提出本角色观点，说明主张、依据与风险。", payload, tier))
        drafts = self._invoke(state, jobs)
        proposals = [{
            "role": role, **{field: required_text(draft.get(field), field) for field in ("title", "summary", "rationale", "risk")},
        } for role, draft in zip(SEATS, drafts)]
        state["proposals"] = numbered("P", proposals)
        state["proposal_release"] = True
        self._artifact(state, "proposals", "四席独立提案", {"items": deepcopy(state["proposals"]), "brief_id": brief["id"]})
        self._move(state, "S1", "N05", title="四路提案收齐并统一公开", event_type="proposals.released")

    def _cluster(self, state):
        raw = self._call(state, "recorder", "cluster", "对已公开提案归类，每条观点标注全部来源提案标识。", {
            "proposals": deepcopy(state["proposals"]), "goal": self._latest(state, "brief_baseline")["content"]["summary"],
        }, "economy")
        claims = raw.get("claims")
        if not isinstance(claims, list) or not 1 <= len(claims) <= 24 or not all(isinstance(item, dict) for item in claims):
            raise InvalidOutput("观点归类结果无效")
        proposal_ids = {item["id"] for item in state["proposals"]}
        clean_claims = []
        for claim in claims:
            sources = text_items(claim.get("source_ids"), "source_ids", 1)
            if not set(sources) <= proposal_ids:
                raise InvalidOutput("观点引用了未知提案")
            clean_claims.append({"text": required_text(claim.get("text"), "claim.text"), "source_ids": sources})
        state["claims"] = numbered("C", clean_claims)
        self._artifact(state, "claim_pool", "结构化观点池", {"items": deepcopy(state["claims"])})
        self._move(state, "S1", "N06", title="登记观点及来源，开始交叉质询")

    def _critique(self, state):
        payload = {
            "kind": "critique", "round": state["round"], "claims": deepcopy(state["claims"]),
            "proposals": deepcopy(state["proposals"]), "objections": deepcopy(state["objections"]),
            "decisions": deepcopy(state["decisions"]),
        }
        jobs = [(role, "基于公开观点独立表态，说明理由；每条分歧需绑定观点，标注类型和阻塞性。",
                 payload, "strong" if role == "challenger" else "balanced") for role in SEATS]
        reviews = self._invoke(state, jobs)
        known_claims = {item["id"] for item in state["claims"]}
        objections = deepcopy(state["objections"])
        stances = []
        for role, review in zip(SEATS, reviews):
            stance = review.get("stance")
            if stance not in ("support", "conditional", "oppose"):
                raise InvalidOutput("质询结果缺少明确立场")
            stances.append({
                "role": role, "stance": stance, "rationale": required_text(review.get("rationale"), "rationale"),
                "round": state["round"],
            })
            pending = review.get("objections")
            if not isinstance(pending, list) or len(pending) > 12 or not all(isinstance(item, dict) for item in pending):
                raise InvalidOutput("异议清单格式无效")
            for item in pending:
                kind, claim_id, blocking = item.get("type"), item.get("claim_id"), item.get("blocking")
                if (kind not in ("fact", "value", "tradeoff") or not isinstance(claim_id, str)
                        or claim_id not in known_claims or type(blocking) is not bool):
                    raise InvalidOutput("异议类型或来源观点无效")
                summary = required_text(item.get("summary"), "objection.summary")
                if any(ob["claim_id"] == claim_id and ob["summary"] == summary for ob in objections):
                    continue
                objections.append({
                    "id": f"O-{len(objections) + 1}", "type": kind, "claim_id": claim_id, "role": role,
                    "blocking": blocking, "summary": summary, "status": "open", "round": state["round"],
                })
        state.update(stances=stances, objections=objections)
        self._artifact(state, "review_round", f"第 {state['round']} 轮表态与分歧", {
            "stances": deepcopy(stances), "objections": deepcopy(objections),
        })
        self._move(state, "S1", "N07", title="公开本轮质询与各席位表态")

    def _counterexamples(self, state):
        challenger = next(item for item in state["stances"] if item["role"] == "challenger")
        self._artifact(state, "challenge", "独立质疑与反例", {
            "review": deepcopy(challenger),
            "objection_ids": [item["id"] for item in state["objections"] if item["role"] == "challenger"],
        })
        self._move(state, "S1", "N08", title="按分歧类型安排处理路径")

    @staticmethod
    def _blocking(state):
        return [item for item in state["objections"] if item["status"] == "open" and item["blocking"]]

    def _route(self, state):
        blocking = self._blocking(state)
        value = next((item for item in blocking if item["type"] == "value"), None)
        if value:
            self._move(state, "S1", "N09", "waiting_human", waiting={
                "id": "HITL-2", "title": "价值取舍待处理", "description": value["summary"], "objection_id": value["id"],
            }, title="价值分歧等待人工决定")
        elif blocking:
            self._move(state, "S1", "N08", "blocked", title="等待补证或权衡依据", details={
                "objection_ids": [item["id"] for item in blocking],
            })
        else:
            self._move(state, "S1", "N10", title="关键分歧已处理，形成可比较方向")

    def _synthesize(self, state):
        candidates = [{"id": claim["id"], "summary": claim["text"], "sources": claim["source_ids"]}
                      for claim in state["claims"][:3]]
        if len(candidates) < 2:
            candidates = [{"id": item["id"], "summary": item["summary"], "sources": [item["id"]]}
                          for item in state["proposals"][:3]]
        state["candidates"] = candidates
        self._artifact(state, "candidates", "候选方向与取舍记录", {
            "candidates": candidates, "decisions": deepcopy(state["decisions"]),
            "unresolved": [item["id"] for item in state["objections"] if item["status"] == "open"],
        })
        self._move(state, "S1", "N11", title="候选方向已整理，执行 G1")

    def _check_consensus(self, state):
        stances = {item["role"]: item["stance"] for item in state["stances"] if item["round"] == state["round"]}
        support_count = sum(stances.get(role) == "support" for role in SEATS)
        gate = self._gate(state, "G1", [
            ("四份独立提案已汇合", state["proposal_release"]),
            ("至少两个候选方向可比较", len(state["candidates"]) >= 2),
            ("没有尚未解决的阻塞异议", not self._blocking(state)),
            ("所有必需席位均已表态", set(SEATS) <= stances.keys()),
            ("有效赞同达到 80%", support_count / len(SEATS) >= SUPPORT_THRESHOLD),
            ("讨论轮次数在限制内", state["round"] <= MAX_ROUNDS),
        ])
        gate.update(support_count=support_count, seat_count=len(SEATS), threshold=SUPPORT_THRESHOLD)
        if gate["passed"]:
            self._move(state, "S2", "N12", title="G1 通过，进入建设方案", details={"gate": gate}, event_type="gate.checked")
        else:
            at_limit = state["round"] >= MAX_ROUNDS
            if at_limit:
                self._artifact(state, "conditional_result", "讨论上限与待决清单", {
                    "gate": gate, "blocking": self._blocking(state),
                    "resume_condition": "重新框定目标或补足证据后重新开始。",
                })
            self._move(state, "S1", "N11", "blocked", title="G1 未通过，保留待决项" if at_limit else "G1 未通过，可继续讨论",
                       details={"gate": gate}, event_type="gate.checked")

    def _plan(self, state):
        candidates = deepcopy(state["candidates"])
        raw = self._call(state, "recorder", "plan", "整理建设方案，说明范围、路径、资源、风险和验证方法。"
                         "按定题约束比较候选方向，在 approach 中说明保留哪些候选方向及理由。", {
            "brief": self._latest(state, "brief_baseline")["content"], "claims": deepcopy(state["claims"]),
            "candidates": candidates, "decisions": deepcopy(state["decisions"]),
            "revision_note": state.get("_revision_note", ""),
        }, "strong")
        plan = {field: required_text(raw.get(field), field) for field in ("title", "scope", "approach", "resources")}
        plan.update(id="PLAN-1", candidates=candidates, risks=text_items(raw.get("risks"), "risks", 1),
                    validation=text_items(raw.get("validation"), "validation", 1))
        self._artifact(state, "plan", "建设方案", plan)
        gate = self._gate(state, "G2", [("范围、路径和资源齐全", True), ("风险及验证方式已说明", True)])
        self._move(state, "S3", "N13", title="G2 通过，提交方案评价", details={"gate": gate}, event_type="gate.checked")

    def _evaluate(self, state):
        plan = self._latest(state, "plan")
        brief = self._latest(state, "brief_baseline")
        inputs = {
            "plan_id": plan["id"], "plan_version": plan["version"],
            "brief_id": brief["id"], "brief_version": brief["version"],
            "objection_ids": [objection["id"] for objection in state["objections"]],
        }
        payload = {
            "kind": "evaluation", "references": inputs, "dimensions": list(EVALUATION_DIMENSIONS),
            "plan": deepcopy(plan["content"]), "brief": deepcopy(brief["content"]),
            "objections": deepcopy(state["objections"]),
        }
        jobs = [(role, f"独立评价并重点检查{focus}。按 dimensions 逐项给 1–5 分，5 分最佳。"
                       "违反任一硬约束时，将 passed 设为 false 并在 constraint_violations 中逐项列出原因。",
                 payload, tier) for role, focus, tier in EVALUATORS]
        raw_reviews = self._invoke(state, jobs)
        reviews = [self._clean_evaluation(raw, role, focus, inputs)
                   for (role, focus, _tier), raw in zip(EVALUATORS, raw_reviews)]
        passed = all(review["passed"] for review in reviews)
        violations = list(dict.fromkeys(item for review in reviews for item in review["constraint_violations"]))
        scores = [
            {"dimension": dimension, "score": round(sum(review["scores"][index]["score"] for review in reviews) / len(reviews), 2),
             "review_count": len(reviews)}
            for index, dimension in enumerate(EVALUATION_DIMENSIONS)
        ]
        evaluation = {
            "passed": passed, "reason": " / ".join(f"{ROLE_LABELS[review['role']]}：{review['reason']}" for review in reviews),
            "scores": scores, "score_scale": {"min": 1, "max": 5}, "reviews": reviews,
            "plan_id": plan["id"], "constraint_violations": violations,
        }
        self._artifact(state, "evaluation", "独立评价与评分依据", evaluation)
        gate = self._gate(state, "G3", [
            ("产品、用户和技术评价均通过", passed), ("无已登记的硬约束违反", not violations),
            ("无尚未解决的阻塞异议", not self._blocking(state)),
        ])
        if gate["passed"]:
            self._move(state, "S4", "N14", title="G3 通过，形成需求规格", details={"gate": gate}, event_type="gate.checked")
        else:
            self._move(state, "S3", "N13", "blocked", title="G3 未通过，等待退回方案", details={"gate": gate}, event_type="gate.checked")

    @staticmethod
    def _clean_evaluation(raw, role, focus, inputs):
        if type(raw.get("passed")) is not bool:
            raise InvalidOutput("评价结果缺少明确结论")
        scores = raw.get("scores")
        if not isinstance(scores, list) or len(scores) != len(EVALUATION_DIMENSIONS) or not all(isinstance(item, dict) for item in scores):
            raise InvalidOutput("评价结果缺少评分明细")
        clean_scores = {}
        for item in scores:
            score = item.get("score")
            if type(score) not in (int, float) or not 1 <= score <= 5:
                raise InvalidOutput("评价分数无效")
            dimension = required_text(item.get("dimension"), "dimension", 80)
            if dimension not in EVALUATION_DIMENSIONS or dimension in clean_scores:
                raise InvalidOutput("评价维度未知或重复")
            clean_scores[dimension] = score
        violations = text_items(raw.get("constraint_violations", []), "constraint_violations")
        return {
            "role": role, "focus": focus, "inputs": deepcopy(inputs),
            "verdict": raw["passed"], "passed": raw["passed"] and not violations,
            "reason": required_text(raw.get("reason"), "reason"),
            "scores": [{"dimension": dimension, "score": clean_scores[dimension]} for dimension in EVALUATION_DIMENSIONS],
            "constraint_violations": violations,
        }

    def _requirements(self, state):
        plan = self._latest(state, "plan")
        raw = self._call(state, "recorder", "requirements", "将推荐方案写为可测试的需求，每条注明验收标准和来源标识。"
                         "如收到签收退回意见，按意见修订并遵守已确认的约束。", {
            "plan": deepcopy(plan["content"]), "claims": deepcopy(state["claims"]),
            "evaluation": self._latest(state, "evaluation")["content"],
            "revision_note": state.get("_spec_feedback", ""),
        }, "balanced")
        rows = raw.get("requirements")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20 or not all(isinstance(item, dict) for item in rows):
            raise InvalidOutput("需求规格需要包含可核验的条目")
        known = {item["id"] for item in state["claims"]} | {plan["id"], plan["content"]["id"]}
        clean = []
        for row in rows:
            source = required_text(row.get("source_id"), "source_id", 120)
            if source not in known:
                raise InvalidOutput("需求引用了未知方案或观点")
            clean.append({"title": required_text(row.get("title"), "title", 240),
                          "acceptance": required_text(row.get("acceptance"), "acceptance"), "source_id": source})
        draft = self._artifact(state, "specification", "需求规格草案", {
            "requirements": numbered("REQ", clean), "plan_id": plan["id"],
        })
        gate = self._gate(state, "G4", [("每条需求可追溯且可验收", True)])
        self._move(state, "S5", "N15", "waiting_human", waiting={
            "id": "HITL-3", "title": "需求基线待签收", "description": "核对需求范围、验收标准和追溯依据。",
            "artifact_id": draft["id"],
        }, title="G4 通过，等待最终需求确认", details={"gate": gate}, event_type="gate.checked")

    def _decision(self, state, action, note, **details):
        decision = {"id": f"D-{len(state['decisions']) + 1}", "node": state["node"], "action": action,
                    "note": note, "timestamp": utcnow(), "version": state["version"], "actor": "本地主持人", **details}
        state["decisions"].append(decision)
        return decision

    def _approve(self, state, note, receipt):
        node = state["node"]
        waiting = state["waiting"]
        decision = self._decision(state, "approve", note, artifact_id=waiting.get("artifact_id"), objection_id=waiting.get("objection_id"))
        details = {"decision": decision, "request_id": waiting["id"]}
        if node == "N02":
            self._move(state, "S0", "N03", title="主持人已确认议题", details=details, event_type="human.approved", **receipt)
        elif node == "N09":
            objection = next(item for item in state["objections"] if item["id"] == waiting["objection_id"])
            objection.update(status="resolved", resolution=note, decision_id=decision["id"])
            self._after_resolution(state, details, receipt)
        elif node == "N15":
            if self._blocking(state):
                raise WorkflowError("仍有阻塞异议，无法签收。", 409, "blocking_objection")
            spec = self._latest(state, "specification")
            baseline = self._artifact(state, "baseline", "已签收的需求基线", {
                **deepcopy(spec["content"]), "source_id": spec["id"], "confirmation": decision,
            })
            gate = self._gate(state, "G5", [("当前版本与范围已确认", True), ("无阻塞项", True)])
            self._move(state, "DEV", "DEV-C", title="G5 通过并冻结需求基线", details={
                **details, "gate": gate, "baseline_id": baseline["id"],
            }, event_type="human.approved", **receipt)
        elif node == "DEV-A":
            self._artifact(state, "delivery_acceptance", "原型交付确认", {
                "confirmation": decision, "review_id": self._latest(state, "implementation_review")["id"], "simulated": True,
            })
            self._move(state, "DEV", "DEV-A", "completed", title="原型交付已验收", details=details, event_type="run.completed", **receipt)

    def _resolve_objection(self, state, objection_id, note, receipt):
        objection = next((item for item in state["objections"] if item["id"] == objection_id), None)
        if objection is None or not self._open_nonvalue(objection):
            raise WorkflowError("请选择待处理的事实或权衡异议。", code="invalid_objection")
        decision = self._decision(state, "resolve_objection", note, objection_id=objection_id)
        objection.update(status="resolved", resolution=note, decision_id=decision["id"])
        self._after_resolution(state, {"decision": decision}, receipt)

    def _after_resolution(self, state, details, receipt):
        blocking = self._blocking(state)
        other_value = next((item for item in blocking if item["type"] == "value"), None)
        if other_value:
            self._move(state, "S1", "N09", "waiting_human", waiting={
                "id": "HITL-2", "title": "价值取舍待处理", "description": other_value["summary"],
                "objection_id": other_value["id"],
            }, title="已记录取舍，继续处理剩余价值分歧", details=details, event_type="objection.resolved", **receipt)
        elif blocking:
            self._move(state, "S1", "N08", "blocked", title="已记录处理依据，仍有阻塞异议",
                       details=details, event_type="objection.resolved", **receipt)
        elif state["round"] >= MAX_ROUNDS:
            state["stances"] = []
            self._move(state, "S1", "N11", title="已记录处理依据，按轮次上限执行 G1",
                       details=details, event_type="objection.resolved", **receipt)
        else:
            self._reopen_discussion(state)
            self._move(state, "S1", "N06", title="依据新决定重新获取各席位立场",
                       details=details, event_type="objection.resolved", **receipt)

    @staticmethod
    def _reopen_discussion(state):
        state.update(stage="S1", node="N06", status="ready", round=state["round"] + 1, stances=[], waiting=None)

    def _return(self, state, note, receipt):
        node = state["node"]
        decision = self._decision(state, "return", note)
        state["_revision_note"] = note
        if node in ("N02", "N11"):
            state.update(proposals=[], claims=[], stances=[], objections=[], candidates=[], round=1,
                         work_plan=[], proposal_release=False, _brief_feedback=note)
            stage, target = "S0", "N01"
        elif node == "N09" or node == "N08":
            if state["round"] >= MAX_ROUNDS:
                stage, target = "S1", "N11"
            else:
                self._reopen_discussion(state)
                stage, target = "S1", "N06"
        elif node == "N13":
            stage, target = "S2", "N12"
        elif node == "N15":
            state["_spec_feedback"] = note
            stage, target = "S4", "N14"
        else:
            stage, target = "DEV", "DEV-C"
        self._move(state, stage, target, title=f"已退回{NODE_LABELS[target]}", details={"decision": decision},
                   event_type="human.returned", **receipt)

    def _implementation(self, state):
        baseline = self._latest(state, "baseline")
        tasks = [{"requirement_id": item["id"], "title": item["title"], "acceptance": item["acceptance"]}
                 for item in baseline["content"]["requirements"]]
        self._artifact(state, "implementation", "模拟实现交接", {"baseline_id": baseline["id"], "tasks": tasks, "simulated": True})
        self._move(state, "DEV", "DEV-T", title="已生成模拟实现交接单")

    def _testing(self, state):
        implementation = self._latest(state, "implementation")
        coverage = [{"requirement_id": item["requirement_id"], "check": "存在可检查的验收条件", "passed": bool(item["acceptance"])}
                    for item in implementation["content"]["tasks"]]
        self._artifact(state, "implementation_test", "模拟验收覆盖检查", {
            "implementation_id": implementation["id"], "checks": coverage, "simulated": True,
        })
        self._move(state, "DEV", "DEV-R", title="完成模拟覆盖检查")

    def _review(self, state):
        tests = self._latest(state, "implementation_test")
        review = self._artifact(state, "implementation_review", "模拟交付评审", {
            "test_id": tests["id"], "baseline_id": self._latest(state, "baseline")["id"],
            "scope": "需求和验收条件的关联检查", "simulated": True,
        })
        self._move(state, "DEV", "DEV-A", "waiting_human", waiting={
            "id": "DELIVERY", "title": "确认原型交付", "description": "查看需求基线、模拟实现、验收覆盖与评审记录。",
            "artifact_id": review["id"],
        }, title="等待人工确认原型交付")
