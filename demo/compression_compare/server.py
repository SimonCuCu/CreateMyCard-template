"""本地比较页后端：同一 query 分别走两套真实 Compact 出卡服务。"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets

HOST = "127.0.0.1"
PORT = int(os.getenv("CREATE_MY_CARD_COMPARISON_DEMO_PORT", "8870"))
APP_VERSION = "11.7.5.205"
ROM_VERSION = "CLS-AL30 6.0.0.328"
_STREAM_HEADER = re.compile(
    r"^type=(?P<type>'(?:\\.|[^'])*') tool=(?P<tool>'(?:\\.|[^'])*') "
    r"operation=(?P<operation>'(?:\\.|[^'])*') requestId=(?P<request_id>None|'(?:\\.|[^'])*')$"
)


@dataclass(frozen=True)
class ServiceTarget:
    label: str
    port: int

    @property
    def ws_base_url(self) -> str:
        return f"ws://{HOST}:{self.port}/api/v1/ws/tools"


ORIGINAL = ServiceTarget("原组件库（未压缩）", 8855)
COMPRESSED = ServiceTarget("压缩组件库", 8856)
PLANNER_API_URL = os.getenv(
    "CREATE_MY_CARD_COMPARISON_PLANNER_API_URL",
    "https://api.deepseek.com/chat/completions",
)
PLANNER_MODEL = os.getenv("CREATE_MY_CARD_COMPARISON_PLANNER_MODEL", "deepseek-chat")


def _planner_api_key() -> str:
    value = os.getenv("WIDGET_SERVICE_DEEPSEEK_API_KEY") or os.getenv(
        "DEEPSEEK_API_KEY"
    )
    if not value:
        raise RuntimeError(
            "缺少 WIDGET_SERVICE_DEEPSEEK_API_KEY，无法调用宿主 Main Agent"
        )
    return value


def _json_object(content: str) -> dict[str, object]:
    """解析宿主 Main Agent 的单个 JSON 对象。"""
    fenced = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", content, re.DOTALL)
    parsed = json.loads(fenced.group(1) if fenced else content)
    if not isinstance(parsed, dict):
        raise TypeError("宿主 Main Agent 未返回 JSON 对象")
    return parsed


async def _main_agent_json(
    system: str, payload: dict[str, object], phase: str
) -> dict[str, object]:
    request = {
        "model": PLANNER_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    headers = {"Authorization": f"Bearer {_planner_api_key()}"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(PLANNER_API_URL, json=request, headers=headers)
    response.raise_for_status()
    response_json = response.json()
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError(f"宿主 Main Agent {phase} 响应缺少内容") from exc
    if not isinstance(content, str):
        raise TypeError(f"宿主 Main Agent {phase} 响应不是文本")
    return _json_object(content)


def _capability_ids(overview: dict[str, object], field: str) -> set[str]:
    values = overview.get(field, [])
    if not isinstance(values, list):
        raise TypeError(f"能力概述的 {field} 不是数组")
    return {
        item["id"]
        for item in values
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _assert_same_catalog(
    original: dict[str, object], compressed: dict[str, object], field: str
) -> None:
    if _capability_ids(original, field) != _capability_ids(compressed, field):
        raise ValueError(f"两套仓库的 {field} 不一致，不能进行公平对比")


async def _select_host_semantics(
    query: str, overview: dict[str, object]
) -> dict[str, object]:
    system = (
        "你是小艺创建桌面卡片的宿主 Main Agent。只根据本轮能力概述选择候选，"
        "不选择组件、模板或 DSL。仅输出 JSON：title、description、size、"
        "dataCapabilityIds、candidateAssetIds、candidateEventIds。"
        "三个候选字段均为数组，且只能使用概述中的 ID。"
        "用户未请求点击时 candidateEventIds 必须为空。"
    )
    selection = await _main_agent_json(
        system, {"query": query, "overview": overview}, "语义选择"
    )
    for field, overview_field in (
        ("dataCapabilityIds", "dataCapabilities"),
        ("candidateAssetIds", "assetCandidates"),
        ("candidateEventIds", "eventCapabilities"),
    ):
        values = selection.get(field, [])
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise TypeError(f"宿主 Main Agent 的 {field} 不合法")
        if not set(values).issubset(_capability_ids(overview, overview_field)):
            raise ValueError(f"宿主 Main Agent 的 {field} 包含概述外 ID")
    if not selection["dataCapabilityIds"]:
        raise ValueError("宿主 Main Agent 没有选择可用于本 Demo 的数据能力")
    return selection


async def _build_shared_plan(
    query: str,
    overview: dict[str, object],
    schemas: dict[str, object],
    selection: dict[str, object],
) -> dict[str, object]:
    system = (
        "你是小艺创建桌面卡片的宿主 Main Agent。根据 query、已选能力和本轮完整 Schema，"
        "只输出 JSON：title、description、size、candidateDataBindings。"
        "每个 binding 必须有 capabilityId、arguments、writeResultTo、candidateOutputFields。"
        "capabilityId 只能来自 selectedDataCapabilityIds，字段必须来自 outputSchema。"
        "不要输出组件、模板、DSL、事件或素材。"
    )
    payload = {
        "query": query,
        "overview": overview,
        "selectedDataCapabilityIds": selection["dataCapabilityIds"],
        "schemas": schemas,
    }
    plan = await _main_agent_json(system, payload, "数据绑定")
    bindings = plan.get("candidateDataBindings")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("宿主 Main Agent 未生成数据绑定")
    selected_ids = set(selection["dataCapabilityIds"])
    for binding in bindings:
        if not isinstance(binding, dict):
            raise TypeError("宿主 Main Agent 的数据绑定不是对象")
        if binding.get("capabilityId") not in selected_ids:
            raise ValueError("宿主 Main Agent 的数据绑定引用了未选能力")
        if not isinstance(binding.get("arguments"), dict):
            raise TypeError("宿主 Main Agent 的 arguments 不合法")
        write_result_to = binding.get("writeResultTo")
        if not isinstance(write_result_to, str) or not write_result_to.startswith(
            "/data/"
        ):
            raise ValueError("宿主 Main Agent 的 writeResultTo 必须位于 /data/")
        fields = binding.get("candidateOutputFields")
        if not isinstance(fields, list) or not all(
            isinstance(item, str) for item in fields
        ):
            raise TypeError("宿主 Main Agent 的 candidateOutputFields 不合法")
    for field in ("title", "description", "size"):
        if not isinstance(plan.get(field), str) or not plan[field]:
            raise ValueError(f"宿主 Main Agent 未生成有效 {field}")
    event_by_id = {
        item["id"]: item
        for item in overview.get("eventCapabilities", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    event_candidates = []
    for event_id in selection["candidateEventIds"]:
        action = event_by_id[event_id].get("actionTemplate")
        if not isinstance(action, dict):
            raise TypeError(f"事件 {event_id} 缺少 actionTemplate")
        event_candidates.append({"eventId": event_id, "action": deepcopy(action)})
    return {
        "title": plan["title"],
        "description": plan["description"],
        "size": plan["size"],
        "candidateDataBindings": bindings,
        "candidateEventCandidates": event_candidates,
        "candidateAssetIds": selection["candidateAssetIds"],
    }


def _envelope(
    content: dict[str, object], query: str, interaction_id: str
) -> dict[str, object]:
    return {
        "content": {"odid": "comparison-demo-device", **content},
        "deviceInfo": {
            "countryCode": "CN",
            "deviceFormation": "phone",
            "deviceType": 0,
            "locale": "zh-CN",
            "prdVer": APP_VERSION,
            "romVersion": ROM_VERSION,
        },
        "session": {
            "interactionId": interaction_id,
            "isNew": True,
            "sessionId": "comparison-demo",
        },
        "userAuth": {"user": {"userId": "comparison-demo-user"}},
        "utterance": {"original": query, "type": "text"},
        "version": "1.0",
        "bundleName": "com.omega_w_0823.hmservice",
    }


def _parse_stream_content(value: str) -> dict[str, object]:
    start = value.find("type=")
    if start < 0:
        raise ValueError("服务没有返回可解析的最终消息")
    content = value[start:]
    header, data_and_tail = content.split(" data=", 1)
    data_text, status_and_tail = data_and_tail.rsplit(" status=", 1)
    status_text, error_code_and_tail = status_and_tail.split(" errorCode=", 1)
    error_code_text, _error_text = error_code_and_tail.split(" error=", 1)
    if _STREAM_HEADER.fullmatch(header) is None:
        raise ValueError("服务最终消息格式不受支持")
    data = ast.literal_eval(data_text)
    if not isinstance(data, dict):
        raise TypeError("服务最终消息没有对象形式的业务数据")
    status = ast.literal_eval(status_text)
    error_code = ast.literal_eval(error_code_text)
    if not isinstance(status, str) or not isinstance(error_code, str):
        raise TypeError("服务最终消息状态格式不受支持")
    return {"data": data, "status": status, "errorCode": error_code}


async def _call(
    target: ServiceTarget, operation: str, payload: dict[str, object]
) -> dict[str, object]:
    uri = f"{target.ws_base_url}/{operation}"
    async with websockets.connect(
        uri, open_timeout=5.0, close_timeout=5.0
    ) as websocket:
        await websocket.send(json.dumps(payload, ensure_ascii=False))
        while True:
            message = json.loads(await websocket.recv())
            stream_info = message.get("reply", {}).get("streamInfo", {})
            if stream_info.get("streamType") == "final":
                return _parse_stream_content(str(stream_info.get("streamContent", "")))


def _artifact_block(markdown: str, name: str) -> str:
    marker = f"```{name}\n"
    start = markdown.find(marker)
    if start < 0:
        raise ValueError(f"artifact 缺少 {name} 块")
    content_start = start + len(marker)
    end = markdown.find("\n```", content_start)
    if end < 0:
        raise ValueError(f"artifact 的 {name} 块未闭合")
    return markdown[content_start:end]


def _response_data(
    response: dict[str, object], label: str, step: str
) -> dict[str, object]:
    if response.get("status") != "success":
        raise ValueError(f"{label} {step}失败")
    data = response.get("data")
    if not isinstance(data, dict):
        raise TypeError(f"{label} {step}没有对象形式的业务数据")
    return data


async def _load_overview(target: ServiceTarget, query: str) -> dict[str, object]:
    request_id = uuid.uuid4().hex
    overview = await _call(
        target,
        "getWidgetCapabilityOverview",
        _envelope(
            {"bundleName": "com.omega_w_0823.hmservice"},
            query,
            f"overview-{request_id}",
        ),
    )
    return _response_data(overview, target.label, "能力概述")


async def _load_schemas(
    target: ServiceTarget, query: str, capability_ids: list[str]
) -> dict[str, object]:
    request_id = uuid.uuid4().hex
    schemas = await _call(
        target,
        "getDataCapabilitySchemas",
        _envelope(
            {
                "bundleName": "com.omega_w_0823.hmservice",
                "dataCapabilityIds": capability_ids,
            },
            query,
            f"schema-{request_id}",
        ),
    )
    return _response_data(schemas, target.label, "数据 Schema")


async def _generate_one(
    target: ServiceTarget, query: str, plan: dict[str, object]
) -> dict[str, object]:
    request_id = uuid.uuid4().hex
    result = await _call(
        target,
        "generateWidgetCardCompactDsl",
        _envelope(
            {"bundleName": "com.omega_w_0823.hmservice", "userQuery": query, **plan},
            query,
            f"generate-{request_id}",
        ),
    )
    result_data = result.get("data")
    if not isinstance(result_data, dict):
        raise TypeError(f"{target.label} 出卡没有业务数据")
    artifact_url = result_data.get("artifactUrl")
    is_success = result.get("status") == "success" and result_data.get("status") in {
        "success",
        "degraded",
    }
    if not is_success or not isinstance(artifact_url, str):
        raise ValueError(f"{target.label} 出卡失败：{result_data}")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(artifact_url)
    response.raise_for_status()
    return {
        "label": target.label,
        "artifactUrl": artifact_url,
        "genui": _artifact_block(response.text, "genui"),
        "suggestSize": result_data.get("suggestSize"),
    }


async def _generate_pair(
    query: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    original_overview, compressed_overview = await asyncio.gather(
        _load_overview(ORIGINAL, query),
        _load_overview(COMPRESSED, query),
    )
    for field in ("dataCapabilities", "eventCapabilities", "assetCandidates"):
        _assert_same_catalog(original_overview, compressed_overview, field)
    selection = await _select_host_semantics(query, original_overview)
    selected_data_ids = list(selection["dataCapabilityIds"])
    original_schemas, compressed_schemas = await asyncio.gather(
        _load_schemas(ORIGINAL, query, selected_data_ids),
        _load_schemas(COMPRESSED, query, selected_data_ids),
    )
    if json.dumps(original_schemas, sort_keys=True) != json.dumps(
        compressed_schemas, sort_keys=True
    ):
        raise ValueError("两套仓库的已选数据 Schema 不一致，不能进行公平对比")
    plan = await _build_shared_plan(
        query,
        original_overview,
        original_schemas,
        selection,
    )
    original, compressed = await asyncio.gather(
        _generate_one(ORIGINAL, query, plan),
        _generate_one(COMPRESSED, query, plan),
    )
    return (
        original,
        compressed,
        {
            "selection": selection,
            "plan": plan,
            "permissionGate": {
                "status": "tool_unavailable_default_continue",
                "reason": "本地比较环境未接入端侧 RequestDataPermission 工具",
            },
        },
    )


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(Path(__file__).parent), **kwargs)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/generate":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            query = payload.get("query", "") if isinstance(payload, dict) else ""
            if not isinstance(query, str) or not query.strip():
                raise ValueError("请输入卡片需求")
            original, compressed, planner = asyncio.run(_generate_pair(query.strip()))
            self._write_json(
                HTTPStatus.OK,
                {
                    "query": query.strip(),
                    "original": original,
                    "compressed": compressed,
                    "planner": planner,
                },
            )
        except (
            OSError,
            TypeError,
            ValueError,
            httpx.HTTPError,
            websockets.WebSocketException,
        ) as exc:
            self._write_json(
                HTTPStatus.BAD_GATEWAY, {"error": f"{type(exc).__name__}: {exc}"}
            )

    def _write_json(self, status: HTTPStatus, value: dict[str, object]) -> None:
        content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"Comparison demo ready: http://{HOST}:{PORT}")
    server.serve_forever()
