"""COPA(OpenAI 호환 채팅 API) 호출. 키는 .env의 COPA_API_KEY에서 읽고 찍지 않는다.

COPA는 response_format과 seed를 받지 않는다(tools/wikigen/llm.py). 같은 요청도 결과가 달라서, 평가는 호출마다
응답과 토큰 수를 기록으로 남기고 그 기록으로 판정한다.
"""

import asyncio
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

MAX_TRIES = 5  # 일시 오류(호출 한도, 연결, 서버 오류) 재시도 횟수
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


def copa_settings(env_file: Path = Path(".env")) -> tuple[str, str]:
    """(기본 주소, 키). 환경 변수가 .env보다 먼저다."""
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k.startswith("COPA_")})
    key = values.get("COPA_API_KEY")
    if not key:
        raise SystemExit(".env에 COPA_API_KEY가 없습니다. 형식은 .env.example을 보세요.")
    return values.get("COPA_BASE_URL", "https://copa.codyssey.kr").rstrip("/"), key


class ChatModel:
    """채팅 완성 한 번을 부르고, 토큰 사용량을 모델별로 모은다."""

    def __init__(self, model: str, *, temperature: float | None = 0.0, base_url: str | None = None,
                 api_key: str | None = None, http: httpx.AsyncClient | None = None):
        if base_url is None or api_key is None:
            base_url, api_key = copa_settings()
        self.model = model
        self.temperature = temperature
        self._http = http or httpx.AsyncClient(base_url=f"{base_url}/v1", timeout=300,
                                               headers={"Authorization": f"Bearer {api_key}"})
        self.usage: Counter[str] = Counter()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
                       tool_choice: str | None = None, max_tokens: int = 2000) -> dict[str, Any]:
        """응답 메시지(assistant) 하나. 도구 호출이 있으면 tool_calls에 담겨 온다."""
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_completion_tokens": max_tokens}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        data = await self._post(body)
        usage = data.get("usage") or {}
        self.usage["prompt_tokens"] += usage.get("prompt_tokens") or 0
        self.usage["completion_tokens"] += usage.get("completion_tokens") or 0
        message = data["choices"][0]["message"]
        # 되돌려 보낼 때 null 필드를 빼야 받는 모델이 있다. 도구 호출에 붙은 모델별 필드(예: Gemini의 서명)는 둔다
        return {k: v for k, v in message.items() if v is not None} | {"content": message.get("content") or ""}

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(MAX_TRIES):
            last = attempt == MAX_TRIES - 1
            try:
                response = await self._http.post("/chat/completions", json=body)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if last:
                    raise
                reason = type(e).__name__
            else:
                if response.status_code not in TRANSIENT_STATUS or last:
                    response.raise_for_status()
                    return response.json()
                reason = f"HTTP {response.status_code}"
            wait = 10 * 2**attempt
            print(f"  LLM 일시 오류({reason}), {wait}초 뒤 다시 시도", file=sys.stderr)
            await asyncio.sleep(wait)
        raise AssertionError("도달하지 않는다")
