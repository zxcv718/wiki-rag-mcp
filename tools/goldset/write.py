"""출처마다 LLM이 질문을 쓴다 (ADR-14 편향 대응).

질문 작성자에게는 문서 제목과 본문을 주지 않고 핵심 사실만 준다. 답이 되는 표현을 질문에 넣거나
사실 문장을 그대로 옮기면 검증에서 걸러 다시 쓰게 한다. 질문이 문서 표현을 닮으면 키워드 검색이
유리해져 하이브리드 판정(ADR-02, ADR-12)이 기울기 때문이다.
"""

import json
import re
from pathlib import Path
from typing import Any

from tools.wikigen.plan import extract_json
from tools.wikigen.prompts import retry_message
from tools.wikigen.spec import Spec

BATCH = 15
MAX_ATTEMPTS = 3
COPY_CHARS = 8  # 사실 문장에서 공백을 뺀 이만큼의 글자를 그대로 옮기면 베낀 것으로 본다

SYSTEM = """너는 가상 IT 회사 새솔소프트의 직원들이 사내 AI 비서에게 던질 질문을 쓴다.
질문자는 위키 문서를 본 적이 없고, 문서의 제목이나 표현도 모른다. 평소 동료에게 묻듯이 짧고 자연스럽게 묻는다.
답은 ```json 코드 블록 하나에 JSON 배열만 담아 출력한다."""

GUIDE = {
    "fact": "이 사실이 답이 되는 질문을 쓴다.",
    "procedure": "이 사실이 답이 되도록, 어떻게·언제·누가 하는지 묻는 질문을 쓴다.",
    "permission": "이 사실이 답이 되는 질문을 쓴다. 질문자는 이 내용을 볼 권한이 있는 사람이다.",
    "abbreviation": "용어를 질문에 그대로 넣고, 이 사실이 답이 되는 질문을 쓴다. 용어의 뜻이나 쓰임을 묻는다.",
    "recency": "최근(이번 달, 9월)에 무엇이 바뀌었는지나 바뀐 뒤의 기준을 묻는다. '이번 달', '최근', '9월' 같은 "
               "시간 표현을 넣는다.",
    "contradiction": "주제에 대한 지금 기준을 묻는다. 예전 기준이 있었다는 것이나 연도는 드러내지 않는다.",
    "no_answer": "이 주제에 대해 직원이 물을 법한 질문을 쓴다.",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def copied(question: str, fact: str, keep: str | None = None) -> str | None:
    """사실 문장에서 그대로 옮긴 가장 긴 조각. keep(약어 문항의 용어)은 옮겨도 된다."""
    q, f = _normalize(question), _normalize(fact)
    if keep:
        q = q.replace(_normalize(keep), "|")
    for size in range(len(q), COPY_CHARS - 1, -1):
        for start in range(len(q) - size + 1):
            piece = q[start : start + size]
            if "|" not in piece and piece in f:
                return piece
    return None


def describe_asker(spec: Spec, user: str) -> str:
    return spec.team_names([f"group:{g}" for g in spec.users[user]])


def item_text(spec: Spec, src: dict[str, Any]) -> str:
    lines = [f"id: {src['id']}", f"유형: {src['type']}", f"질문자 소속: {describe_asker(spec, src['asker'])}"]
    if src.get("term"):
        lines.append(f"용어: {src['term']}")
    if src.get("topic"):
        lines.append(f"주제: {src['topic']}")
    if src.get("change"):
        lines.append(f"이번 달 바뀐 내용: {src['change']}")
    if src.get("fact"):
        lines.append(f"답이 되는 사실: {src['fact']}")
    lines.append(f"할 일: {GUIDE[src['type']]}")
    return "\n".join(lines)


def messages_for(spec: Spec, batch: list[dict[str, Any]]) -> list[dict[str, str]]:
    items = "\n\n".join(item_text(spec, s) for s in batch)
    user = f"""아래 {len(batch)}개 항목마다 질문 하나를 쓴다.

{items}

[규칙]
- 질문은 5~60자의 한국어 한 문장이다. 반말과 존댓말 모두 괜찮다. 예: "연차는 며칠 전에 신청해야 돼?"
- 답이 되는 수치, 기한, 이름, 값을 질문에 넣지 않는다.
- 사실 문장의 표현을 그대로 옮기지 않는다. 문서를 모르는 사람이 쓸 법한 다른 말로 묻는다.
- 약어·용어·에러 코드는 사람이 실제로 부르는 대로 써도 된다. 유형이 abbreviation이면 용어를 꼭 넣는다.
- 질문자 소속에 어울리는 말투와 관심사로 쓴다.

[형식] [{{"id": "q001", "question": "..."}}]"""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def check(batch: list[dict[str, Any]], answer: Any) -> tuple[dict[str, str], list[str]]:
    """검증을 통과한 질문과 문제 목록. 하나라도 틀리면 배치 전체를 다시 쓰게 한다."""
    if not isinstance(answer, list):
        return {}, ["JSON 배열이 아니다"]
    got = {a.get("id"): a.get("question") for a in answer if isinstance(a, dict)}
    problems, questions = [], {}
    for src in batch:
        q = got.get(src["id"])
        if not isinstance(q, str) or not 5 <= len(q.strip()) <= 60:
            problems.append(f"{src['id']}: 질문이 없거나 5~60자가 아니다")
            continue
        q = q.strip()
        must = src.get("must_include")
        if must and must != src.get("term") and _normalize(must) in _normalize(q):
            problems.append(f"{src['id']}: 답인 \"{must}\"를 질문에 넣었다")
        if src["type"] == "abbreviation" and src["term"] not in q:
            problems.append(f"{src['id']}: 용어 \"{src['term']}\"가 질문에 없다")
        for text in (src.get("fact"), src.get("change")):
            if text and (piece := copied(q, text, src.get("term"))):
                problems.append(f"{src['id']}: 사실 문장의 \"{piece}\"를 그대로 옮겼다. 다른 말로 묻는다")
        questions[src["id"]] = q
    extra = set(got) - {s["id"] for s in batch}
    if extra:
        problems.append(f"요청하지 않은 id가 있다: {sorted(map(str, extra))}")
    return questions, problems


def run_write(spec: Spec, sources: list[dict[str, Any]], out: Path, llm) -> int:
    """이미 쓴 문항은 건너뛴다. 끝까지 검증을 못 넘은 배치 수를 돌려준다."""
    done = {}
    if out.exists():
        done = {r["id"]: r["question"] for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    todo = [s for s in sources if s["id"] not in done]
    failed = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        messages = messages_for(spec, batch)
        questions: dict[str, str] = {}
        problems: list[str] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            text, meta = llm.chat(messages, max_tokens=4000)
            try:
                questions, problems = check(batch, extract_json(text))
            except ValueError as e:
                questions, problems = {}, [f"JSON을 읽을 수 없다: {e}"]
            llm.record({"step": "question", "target": f"{batch[0]['id']}~{batch[-1]['id']}", "attempt": attempt,
                        **meta, "problems": problems})
            if not problems:
                break
            messages = [*messages, {"role": "assistant", "content": text}, retry_message(problems)]
        else:
            failed += 1
            print(f"{batch[0]['id']}~{batch[-1]['id']}: 검증 실패, 다음에 다시 쓴다 ({problems[:3]})")
            continue
        with out.open("a", encoding="utf-8") as f:
            for s in batch:
                f.write(json.dumps({"id": s["id"], "question": questions[s["id"]]}, ensure_ascii=False) + "\n")
        print(f"{batch[0]['id']}~{batch[-1]['id']} 완료 (시도 {attempt}회)")
    return failed
