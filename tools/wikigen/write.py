"""2단계: 본문 생성. manifest의 문서마다 본문을 쓰고, 검증에 실패하면 무엇이 틀렸는지 알려 주며 다시 쓴다."""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tools.wikigen.plan import load_manifest
from tools.wikigen.prompts import retry_message, write_messages
from tools.wikigen.spec import ALL, Spec

MAX_ATTEMPTS = 3
MIN_CHARS, MAX_CHARS = 600, 5000
FORBIDDEN = {"—": "em dash", "→": "화살표", "⇒": "화살표", "➡": "화살표"}
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
INJECTION_LIKE = re.compile(r"(이전|앞선) 지시를 (모두 )?무시")
# 작성 규칙이 본문으로 새어 나온 흔적. 실제 위키에는 없을 문장이다
PROMPT_LEAK = re.compile(
    r"이미 (알고 있는|아는) 말처럼|뜻을 (따로 )?설명하(는|지 않)|핵심 사실|작성 규칙|쓸 수 있는 (사내 )?용어"
)
# 9장: 인덱싱 전에 막을 민감정보 형식. 생성 문서에 실제 값처럼 보이는 것이 섞이지 않게 한다
SECRETS = {
    "AWS 액세스 키": re.compile(r"AKIA[0-9A-Z]{16}"),
    "주민등록번호": re.compile(r"\b\d{6}-[1-4]\d{6}\b"),
    "API 키": re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
}


def normalize(text: str, title: str) -> str:
    """코드 블록으로 감싼 응답을 풀고, 첫 줄을 계획한 제목의 H1으로 맞춘다."""
    text = text.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return f"# {title}\n\n" + "\n".join(lines).strip() + "\n"


def check_body(doc: dict[str, Any], body: str, spec: Spec) -> list[str]:
    """본문이 계획과 규칙을 지켰는지 본다. write 단계의 재시도와 lint가 같이 쓴다."""
    problems = []
    if not body.startswith(f"# {doc['title']}\n"):
        problems.append("첫 줄이 계획한 제목의 H1이 아니다")
    if len(re.findall(r"^## ", body, re.M)) < 2:
        problems.append("## 절이 2개보다 적다")
    if not MIN_CHARS <= len(body) <= MAX_CHARS:
        problems.append(f"본문이 {len(body)}자다. {MIN_CHARS}~{MAX_CHARS}자여야 한다")
    for f in doc["key_facts"]:
        if f["must_include"] not in body:
            problems.append(f"핵심 사실의 표현 \"{f['must_include']}\"가 본문에 글자 그대로 없다")
    for term in doc["terms"]:
        if term not in body:
            problems.append(f"용어 \"{term}\"가 본문에 없다")
    if doc.get("change") and "## 변경 이력" not in body:
        problems.append("이번 달에 바뀐 문서인데 ## 변경 이력 절이 없다")
    if doc.get("injection"):
        if doc["injection"] not in body:
            problems.append("심어야 할 문장이 글자 그대로 들어가지 않았다")
    elif INJECTION_LIKE.search(body):
        problems.append("지시문을 심지 않기로 한 문서에 지시를 무시하라는 문장이 있다")
    if leak := PROMPT_LEAK.search(body):
        problems.append(f"작성 규칙을 본문에 옮겨 적었다(\"{leak.group(0)}\"). 독자에게 필요한 내용만 남긴다")
    problems += [f"{name}({ch})를 썼다" for ch, name in FORBIDDEN.items() if ch in body]
    if EMOJI.search(body):
        problems.append("이모지를 썼다")
    problems += [f"다루지 않을 주제의 말 \"{k}\"가 있다" for k in spec.absent_keywords if k in body]
    problems += [f"용어 사전에 없는 코드 {c}를 만들었다" for c in spec.unknown_codes(body)]
    problems += [f"{name} 형식의 문자열이 있다" for name, pattern in SECRETS.items() if pattern.search(body)]
    return problems


def front_matter(doc: dict[str, Any]) -> str:
    lines = [
        "---",
        f"doc_id: {doc['doc_id']}",
        f"title: {json.dumps(doc['title'], ensure_ascii=False)}",
        f"space: {doc['space']}",
        f"version: {doc['version']}",
        f"revision: {doc['revision']}",
        f"updated_at: {doc['updated_at']}",
    ]
    if doc["restricted"] != [ALL]:
        lines.append(f"restricted: {json.dumps(doc['restricted'])}")
    if doc.get("classification"):
        lines.append(f"classification: {doc['classification']}")
    return "\n".join([*lines, "---", ""])


def write_doc(llm, spec: Spec, doc: dict[str, Any], avoid_topics: list[str], path: Path) -> tuple[list[str], int]:
    """문서 하나를 쓴다. 끝까지 문제가 남으면 문제가 가장 적은 답을 저장하고 lint가 잡게 한다."""
    messages = write_messages(spec, doc, avoid_topics)
    best: tuple[str, list[str]] | None = None
    attempt = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text, meta = llm.chat(messages, max_tokens=6000)
        body = normalize(text, doc["title"])
        problems = check_body(doc, body, spec)
        if meta["finish_reason"] == "length":
            problems.append("응답이 길이 제한에 걸려 잘렸다")
        llm.record({"step": "write", "target": doc["doc_id"], "attempt": attempt, **meta, "problems": problems})
        if best is None or len(problems) < len(best[1]):
            best = (body, problems)
        if not problems:
            break
        messages = [*messages, {"role": "assistant", "content": text}, retry_message(problems)]
    assert best is not None
    path.write_text(front_matter(doc) + "\n" + best[0], encoding="utf-8")
    return best[1], attempt


class BudgetExceeded(RuntimeError):
    """누적 토큰이 한도를 넘어 새 문서를 시작하지 않았다. 다시 돌리면 남은 문서부터 쓴다."""


def run_write(spec: Spec, wiki_dir: Path, llm, *, workers: int = 4, limit: int | None = None,
              only: list[str] | None = None, force: bool = False, budget: int | None = None) -> int:
    manifest = load_manifest(wiki_dir)
    docs_dir = wiki_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    todo = [
        d for d in manifest["documents"]
        if (not only or d["doc_id"] in only) and (force or not (docs_dir / f"{d['doc_id']}.md").exists())
    ][:limit]
    topics = {p["id"]: p["topic"] for p in manifest["pairs"]}

    def one(doc: dict[str, Any]) -> tuple[list[str], int]:
        if budget is not None and llm.used_tokens >= budget:
            raise BudgetExceeded(f"누적 {llm.used_tokens:,} 토큰으로 한도 {budget:,}를 넘었다")
        avoid = [t for pid, t in topics.items() if pid != doc["pair"]]
        return write_doc(llm, spec, doc, avoid, docs_dir / f"{doc['doc_id']}.md")

    left = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, d): d for d in todo}
        for i, future in enumerate(as_completed(futures), 1):
            doc_id = futures[future]["doc_id"]
            try:
                problems, attempts = future.result()
            except Exception as e:  # 한 문서의 실패가 나머지를 멈추지 않게 한다. 다시 돌리면 빠진 문서만 쓴다
                print(f"[{i}/{len(todo)}] {doc_id} 실패: {type(e).__name__}: {e}", file=sys.stderr)
                left += 1
                continue
            left += bool(problems)
            status = "완료" if not problems else "문제 남음: " + "; ".join(problems)
            print(f"[{i}/{len(todo)}] {doc_id} {status} (시도 {attempts}회)")
    return left
