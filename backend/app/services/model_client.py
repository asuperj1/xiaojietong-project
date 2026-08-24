"""大模型推理客户端（Ollama，OpenAI 兼容）。

对接 Ollama 的 /api/chat 流式接口。Ollama 未就绪时优雅降级为占位回复，
保证接口链路可测；真实对话由用户接入微调模型后生效。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.core.config import settings


class ModelClient:
    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式对话，逐块产出文本。失败/未就绪时产出占位说明。"""
        payload = {"model": self.model, "messages": messages, "stream": True}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=payload
                ) as resp:
                    if resp.status_code != 200:
                        yield f"（模型服务返回 {resp.status_code}，请检查 Ollama 是否已启动）"
                        return
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        content = data.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if data.get("done"):
                            break
        except Exception as exc:  # noqa: BLE001 - 降级占位
            yield f"（模型未就绪：{exc}。启动 Ollama 并加载 {self.model} 后体验真实对话）"

    async def chat(self, messages: list[dict]) -> str:
        """非流式单次对话。"""
        chunks = []
        async for chunk in self.stream_chat(messages):
            chunks.append(chunk)
        return "".join(chunks)


model_client = ModelClient()
