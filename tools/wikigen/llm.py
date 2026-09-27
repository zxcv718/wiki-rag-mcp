"""코디세이 Public API(COPA) 호출과 생성 기록.

COPA는 OpenAI 호환 엔드포인트지만 response_format, seed, reasoning_effort를 받지 않는다(2026-09 확인).
그래서 JSON은 응답 텍스트에서 꺼내 검증하고, 같은 요청도 매번 결과가 다르다. 재현성은 호출마다 남기는
생성 기록(프롬프트 해시, 토큰, 검증 결과)과 커밋한 결과물로 확보한다.
"""

import hashlib
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_TRIES = 5  # 일시 오류(호출 한도, 연결, 서버 오류) 재시도 횟수


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith(("COPA_", "WIKIGEN_"))})
    return env


class LLM:
    def __init__(self, log_path: Path, env: dict[str, str] | None = None):
        from openai import OpenAI

        env = env if env is not None else load_env()
        key = env.get("COPA_API_KEY")
        if not key:
            raise SystemExit(".env에 COPA_API_KEY가 없습니다. 형식은 .env.example을 보세요.")
        base = env.get("COPA_BASE_URL", "https://copa.codyssey.kr").rstrip("/")
        self.client = OpenAI(base_url=f"{base}/v1", api_key=key, max_retries=0, timeout=300)
        self.model = env.get("WIKIGEN_MODEL", "gpt-5.4")
        self.log_path = Path(log_path)
        self._lock = threading.Lock()
        summary = usage_summary(self.log_path)
        self.used_tokens = summary["prompt_tokens"] + summary["completion_tokens"]  # 이전 실행까지 포함한 누적

    def _create(self, messages: list[dict[str, str]], max_tokens: int, temperature: float):
        import openai

        transient = (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
                     openai.InternalServerError)
        for attempt in range(MAX_TRIES - 1):
            try:
                return self.client.chat.completions.create(model=self.model, messages=messages,
                                                           max_completion_tokens=max_tokens, temperature=temperature)
            except transient as e:
                wait = 10 * 2**attempt
                print(f"  일시 오류 {type(e).__name__}, {wait}초 뒤 다시 시도", file=sys.stderr)
                time.sleep(wait)
        return self.client.chat.completions.create(model=self.model, messages=messages,
                                                   max_completion_tokens=max_tokens, temperature=temperature)

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int, temperature: float = 0.7) -> tuple[str, dict]:
        """응답 텍스트와 기록용 메타데이터를 돌려준다. 기록은 호출한 쪽이 검증 결과를 붙여 남긴다."""
        started = time.monotonic()
        resp = self._create(messages, max_tokens, temperature)
        usage = resp.usage
        if usage:
            with self._lock:
                self.used_tokens += usage.prompt_tokens + usage.completion_tokens
        meta = {
            "model": resp.model,
            "prompt_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()[:16],
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
            "finish_reason": resp.choices[0].finish_reason,
            "seconds": round(time.monotonic() - started, 1),
        }
        return resp.choices[0].message.content or "", meta

    def record(self, entry: dict[str, Any]) -> None:
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
        with self._lock, self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def usage_summary(log_path: Path) -> dict[str, int]:
    calls = prompt = completion = 0
    if Path(log_path).exists():
        for line in Path(log_path).read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            calls += 1
            prompt += e.get("prompt_tokens") or 0
            completion += e.get("completion_tokens") or 0
    return {"calls": calls, "prompt_tokens": prompt, "completion_tokens": completion}
