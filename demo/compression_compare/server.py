"""本地比较页后端：同一 query 分别走两套真实 Compact 出卡服务。"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import uuid
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


def _plan_query(query: str) -> dict[str, object]:
    """主 Agent 候选规划的本地最小实现；实际出卡仍由微服务完成。"""
    if "电量" in query or "battery" in query.lower():
        return {
            "title": "设备电量",
            "description": "当前电量状态",
            "size": "2x2",
            "candidateDataBindings": [
                {
                    "capabilityId": "GetPhoneBatteryInfo",
                    "arguments": {},
                    "writeResultTo": "/data/phoneBattery",
                    "candidateOutputFields": [
                        "/batterySOC",
                        "/batterySOCText",
                        "/chargingStatusDesc",
                        "/batteryCapacityLevelDesc",
                    ],
                }
            ],
            "candidateEventCandidates": [],
            "candidateAssetIds": ["asset.battery_leaf_fill"],
        }
    city = "上海市" if "上海" in query else "北京市"
    return {
        "title": f"{city}天气",
        "description": "今日天气速览",
        "size": "2x2",
        "candidateDataBindings": [
            {
                "capabilityId": "ViewWeather",
                "arguments": {"prefectureName": city, "forecastDays": 1},
                "writeResultTo": "/data/weather",
                "candidateOutputFields": [
                    "/location/districtName",
                    "/current/temperatureText",
                    "/current/condition",
                    "/current/airQuality",
                ],
            }
        ],
        "candidateEventCandidates": [],
        "candidateAssetIds": [],
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


async def _generate_one(target: ServiceTarget, query: str) -> dict[str, object]:
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
    if overview.get("status") != "success":
        raise ValueError(f"{target.label} 能力概述失败")
    plan = _plan_query(query)
    bindings = plan["candidateDataBindings"]
    assert isinstance(bindings, list)
    capability_ids = [
        item["capabilityId"] for item in bindings if isinstance(item, dict)
    ]
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
    if schemas.get("status") != "success":
        raise ValueError(f"{target.label} 数据 Schema 读取失败")
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


async def _generate_pair(query: str) -> tuple[dict[str, object], dict[str, object]]:
    original, compressed = await asyncio.gather(
        _generate_one(ORIGINAL, query),
        _generate_one(COMPRESSED, query),
    )
    return original, compressed


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
            original, compressed = asyncio.run(_generate_pair(query.strip()))
            self._write_json(
                HTTPStatus.OK,
                {
                    "query": query.strip(),
                    "original": original,
                    "compressed": compressed,
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
