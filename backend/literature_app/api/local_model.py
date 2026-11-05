"""Small OpenAI-compatible local inference port (LM Studio or another local server).

No SDK, tools, retries, model downloads or automatic screening decisions.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx


DEFAULT_MODEL_SETTINGS = {"enabled": False, "base_url": "http://127.0.0.1:1234/v1",
                          "model": "", "max_tokens": 512, "timeout_seconds": 120}


def validate_model_settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_MODEL_SETTINGS):
        raise ValueError("本地模型設定含不支援的欄位")
    result = {**DEFAULT_MODEL_SETTINGS, **value}
    if type(result["enabled"]) is not bool:
        raise ValueError("模型啟用狀態需為布林值")
    base = result["base_url"]
    if not isinstance(base, str) or len(base) > 300 or any(ord(c) < 33 for c in base):
        raise ValueError("模型 API 位址無效")
    try:
        url = urlsplit(base)
        port = url.port
    except ValueError as exc:
        raise ValueError("模型 API 位址無效") from exc
    if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username is not None or url.password is not None or url.query or url.fragment
            or url.path.rstrip("/") != "/v1" or port is None or not 1 <= port <= 65535):
        raise ValueError("本地接口需使用 http://127.0.0.1:1234/v1 形式的 loopback 位址與明確 port")
    result["base_url"] = base.rstrip("/")
    if not isinstance(result["model"], str) or len(result["model"]) > 250 or any(ord(c) < 32 for c in result["model"]):
        raise ValueError("模型名稱無效")
    result["model"] = result["model"].strip()
    if type(result["max_tokens"]) is not int or not 16 <= result["max_tokens"] <= 4096:
        raise ValueError("輸出 token 上限需為 16–4096")
    if type(result["timeout_seconds"]) is not int or not 10 <= result["timeout_seconds"] <= 300:
        raise ValueError("模型逾時需為 10–300 秒")
    return result


class LocalModelClient:
    """Caller owns the httpx client; inject MockTransport for offline tests."""
    def __init__(self, client: httpx.Client, settings: dict):
        self.client = client
        self.settings = validate_model_settings(settings)

    def _request(self, method, endpoint, payload=None):
        token = os.getenv("PUB_TOOL_LM_API_KEY", "")
        headers = {"Authorization": "Bearer " + token} if token else {}
        try:
            response = self.client.request(method, self.settings["base_url"] + endpoint, json=payload,
                                           headers=headers, follow_redirects=False)
            if response.status_code in {401, 403}:
                raise ValueError("LM Studio 驗證失敗，請設定 PUB_TOOL_LM_API_KEY 後重啟 APP")
            if not 200 <= response.status_code < 300:
                raise ValueError(f"本地模型 HTTP {response.status_code}，請查看 LM Studio 的 server log")
            return response.json()
        except httpx.TimeoutException as exc:
            raise ValueError("本地模型回應逾時；未自動重試，請檢查模型載入與 context 設定") from exc
        except httpx.HTTPError as exc:
            # Do not include request headers or a server-provided error body.
            raise ValueError("無法連線本地模型，請確認 LM Studio Server 與 API port") from exc
        except (ValueError, TypeError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(("LM Studio", "本地模型")):
                raise
            raise ValueError("本地模型回傳無效 JSON") from exc

    def models(self):
        result = self._request("GET", "/models")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("模型清單格式無效")
        identifiers = [item.get("id") for item in result["data"] if isinstance(item, dict)]
        if any(not isinstance(name, str) or not name or len(name) > 250 for name in identifiers):
            raise ValueError("模型清單包含無效名稱")
        return list(dict.fromkeys(identifiers))

    def complete(self, messages: list[dict]):
        if not self.settings["enabled"]:
            raise ValueError("請先儲存並啟用本地模型接口")
        if not self.settings["model"]:
            raise ValueError("請先選擇並儲存模型名稱")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 20:
            raise ValueError("模型 messages 無效")
        total = 0
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or message["role"] not in {"system", "user", "assistant"} or not isinstance(message["content"], str)):
                raise ValueError("僅支援純文字 system/user/assistant messages")
            total += len(message["content"])
        if total > 80000:
            raise ValueError("模型輸入超過 80,000 字元，未截斷或傳送；請先縮小輸入")
        result = self._request("POST", "/chat/completions", {
            "model": self.settings["model"], "messages": messages, "temperature": 0.1,
            "max_tokens": self.settings["max_tokens"], "stream": False,
        })
        try:
            choice = result["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
            if not isinstance(content, str) or not content.strip() or finish not in {"stop", "length"}:
                raise ValueError("Empty/unsupported completion")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("模型未回傳有效純文字結果，請確認模型支援 chat completion") from exc
        return {"text": content, "model": self.settings["model"], "finish_reason": finish,
                "truncated": finish == "length"}


def model_http_client(settings):
    # Bypass inherited HTTP proxies for loopback and do not follow redirects.
    return httpx.Client(trust_env=False, follow_redirects=False,
                        timeout=httpx.Timeout(settings["timeout_seconds"], connect=5))
