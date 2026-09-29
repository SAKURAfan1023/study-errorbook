"""TextIn protocol adapter. No automatic resubmission of paid requests."""

import asyncio
import json
import re
from urllib.parse import urlsplit

import httpx

from .models import DomainError

ERASE_ENDPOINT = "https://api.textin.com/ai/service/v1/handwritten_erase"
VLM_CONFIG = {"endpoint": "https://api.textin.com/ai/service/v4/recognize/document", "parameters": {}}
ERASE_PARAMS = {"crop": 0, "doc_direction": 0, "dewarp": 0, "binarization": 0, "image_type": 1}

PARSE_CONFIG = {
    "capabilities": {
        "include_hierarchy": True,
        "include_inline_objects": True,
        "include_char_details": True,
        "include_table_structure": True,
        "pages": True,
        "remove_watermark": False,
        "crop_dewarp": False,
    }
}


class TextIn:
    def __init__(self, app_id: str, secret: str, transport=None):
        self.headers = {"x-ti-app-id": app_id, "x-ti-secret-code": secret}
        self.configured = bool(app_id and secret)
        self.transport = transport

    async def _request(
        self, method: str, url: str, *, authenticated: bool = True,
        receipt: dict | None = None, **kwargs
    ) -> dict:
        if authenticated and not self.configured:
            raise DomainError("credentials_missing", "请在 MCP 服务环境中配置 TextIn 凭据。")
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=60, follow_redirects=False
            ) as client:
                extra_headers = kwargs.pop("extra_headers", {})
                async with client.stream(
                    method,
                    url,
                    headers={**(self.headers if authenticated else {}), **extra_headers},
                    **kwargs,
                ) as response:
                    if receipt is not None:
                        receipt.update(http_status=response.status_code, response_complete=False)
                        receipt["request_ids"] = {
                            key: value for key in ("x-request-id", "x-ti-request-id")
                            if (value := response.headers.get(key)) and len(value) <= 200
                            and not any(secret and secret in value for secret in self.headers.values())
                        }
                    if response.status_code != 200 and receipt is None:
                        raise DomainError(
                            f"http_{response.status_code}", "TextIn 请求未成功；未自动重试。"
                        )
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 32 * 1024 * 1024:
                            raise DomainError("response_too_large", "解析结果超出本地 32MiB 限制。")
                        chunks.append(chunk)
            body = b"".join(chunks)
            if receipt is not None:
                archived = body
                for secret in self.headers.values():
                    if secret:
                        archived = archived.replace(secret.encode(), b"[REDACTED]")
                receipt.update(response_body=archived, response_complete=True,
                               credential_echo_redacted=archived != body)
            if response.status_code != 200:
                raise DomainError(f"http_{response.status_code}", "TextIn 请求未成功；未自动重试。")
            payload = json.loads(body)
        except httpx.HTTPError:
            raise DomainError(
                "network_uncertain", "网络失败；提交是否成功未知，不自动重复提交。"
            ) from None
        except (ValueError, UnicodeError):
            raise DomainError("invalid_response", "TextIn 返回了无法读取的响应。") from None
        if not isinstance(payload, dict):
            raise DomainError("invalid_response", "TextIn 响应应为 JSON 对象。")
        if receipt is not None:
            for key in ("x_request_id", "version"):
                value = payload.get(key)
                if isinstance(value, str) and 0 < len(value) <= 200 and not any(
                    secret and secret in value for secret in self.headers.values()
                ):
                    receipt[key] = value
            for key in ("code", "duration"):
                value = payload.get(key)
                if type(value) in (int, float) and 0 <= value <= 1e12:
                    receipt[key] = value
        code = payload.get("code", 200)
        if type(code) is not int:
            raise DomainError("invalid_response", "TextIn 状态码类型异常。")
        if code != 200:
            messages = {
                40306: "达到调用频率限制；按官方要求停止重试。",
                40003: "TextIn 余额不足。",
                40008: "该 TextIn 产品未开通。",
                40101: "TextIn 凭据缺失。",
                40102: "TextIn 凭据无效。",
                40103: "当前来源不在 TextIn IP 白名单。",
            }
            raise DomainError(f"textin_{code}", messages.get(code, "TextIn 处理失败；未自动重试。"))
        return payload

    async def submit(self, source: bytes, pages: int, suffix: str) -> str:
        config = {**PARSE_CONFIG, "scope": {"page_range": f"1-{pages}"}}
        payload = await self._request(
            "POST",
            "https://api.textin.com/api/v1/xparse/parse/async",
            files={"file": ("document" + suffix, source)},
            data={"config": json.dumps(config)},
        )
        data = payload.get("data")
        job_id = data.get("job_id") if isinstance(data, dict) else None
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", job_id):
            raise DomainError("invalid_response", "响应未提供有效任务 ID；不要自动再次提交。")
        return job_id

    async def poll(self, job_id: str) -> dict:
        payload = await self._request(
            "GET", f"https://api.textin.com/api/v1/xparse/parse/async/{job_id}"
        )
        data = payload.get("data")
        status = data.get("status") if isinstance(data, dict) else None
        if not isinstance(status, str) or status not in {
            "pending",
            "in_progress",
            "completed",
            "failed",
        }:
            raise DomainError("invalid_response", "无法判断 TextIn 任务状态；保留原任务 ID。")
        return data

    async def result(self, url: str) -> dict:
        if not isinstance(url, str) or len(url) > 4096:
            raise DomainError("result_url_rejected", "TextIn 结果地址无效。")
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise DomainError("result_url_rejected", "TextIn结果地址无法解析。") from None
        # Official async docs use web-api.textin.com/file/xparse_result/… .
        # Do not forward credentials to arbitrary response URLs or redirects.
        if (
            parsed.scheme != "https"
            or parsed.netloc not in {"api.textin.com", "web-api.textin.com"}
            or parsed.username
            or parsed.password
        ):
            raise DomainError("result_url_rejected", "结果地址不在已核对的 TextIn 主机范围。")
        payload = await self._request("GET", url)
        return self._result_data(payload)

    @staticmethod
    def _result_data(payload: dict) -> dict:
        data = payload.get("data", payload)
        if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
            raise DomainError("invalid_response", "结果缺少新版 xParse elements 字段。")
        if any(not isinstance(element, dict) for element in data["elements"]):
            raise DomainError("invalid_response", "解析元素格式异常。")
        return data

    async def parse_free(
        self, source: bytes, pages: int, suffix: str, *, receipt: dict | None = None
    ) -> dict:
        """Explicit free synchronous endpoint; never sends account credentials."""
        if len(source) > 10 * 1024 * 1024:
            raise DomainError("free_file_limit", "免费入口单文件上限为10MiB；未上传。")
        config = {**PARSE_CONFIG, "scope": {"page_range": f"1-{pages}"}}
        try:
            async with asyncio.timeout(60):
                payload = await self._request(
                    "POST",
                    "https://api.textin.com/api/v1/agent/parse/sync",
                    authenticated=False, receipt=receipt,
                    extra_headers={"X-From": "study-errorbook"},
                    files={
                        "file": ("document" + suffix, source),
                        "config": (None, json.dumps(config), "application/json"),
                    },
                )
        except TimeoutError:
            raise DomainError(
                "network_uncertain", "免费解析超过60秒，远端是否完成未知；不自动重试。"
            ) from None
        if receipt is not None and receipt.get("credential_echo_redacted"):
            payload = json.loads(receipt["response_body"])
        return self._result_data(payload)

    @staticmethod
    def _vlm_result(payload: dict) -> dict:
        result = payload.get("result") if isinstance(payload, dict) else None
        chunks = result.get("parse_chunk_results") if isinstance(result, dict) else None
        if (not isinstance(chunks, list)
                or any(not isinstance(chunk, dict)
                       or ("text" in chunk and not isinstance(chunk["text"], str))
                       for chunk in chunks)):
            raise DomainError("invalid_response", "VLM结果缺少有效parse_chunk_results；不转换成xParse元素。")
        return result

    async def parse_vlm(self, source: bytes, *, receipt: dict) -> dict:
        """Explicit image-only caller; preserve the independent VLM wire format and receipt."""
        try:
            async with asyncio.timeout(60):
                payload = await self._request(
                    "POST", VLM_CONFIG["endpoint"], content=source,
                    extra_headers={"Content-Type": "application/octet-stream"}, receipt=receipt,
                )
        except TimeoutError:
            raise DomainError("network_uncertain", "VLM解析超过60秒，远端是否完成未知；不自动重试。") from None
        # If the provider echoes a credential, the readable cache must be redacted too.
        if receipt.get("credential_echo_redacted"):
            payload = json.loads(receipt["response_body"])
        self._vlm_result(payload)
        return payload

    async def erase(self, png: bytes, *, receipt: dict | None = None) -> dict:
        return await self._request(
            "POST",
            ERASE_ENDPOINT,
            params=ERASE_PARAMS,
            content=png,
            extra_headers={"Content-Type": "application/octet-stream"},
            receipt=receipt,
        )
