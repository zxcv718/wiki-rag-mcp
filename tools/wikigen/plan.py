"""1단계: 문서 계획.

심을 문서는 plants.yaml에서 그대로 가져오고, 스페이스마다 나머지 문서의 제목과 핵심 사실만 LLM에게 맡긴다.
문서 id, 수정일, 버전은 LLM이 아니라 스키마의 시드로 정한다. 최근 문서와 오래된 문서 수를 목표치에
정확히 맞추고, 같은 계획이면 같은 manifest가 나오게 하기 위해서다.
"""

import json
import random
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from tools.wikigen.prompts import plan_messages, retry_message
from tools.wikigen.spec import ALL, Spec

KST = timezone(timedelta(hours=9))
STALE_DAYS = 365  # 서버가 "오래된 문서"를 붙이는 기준과 같다 (server/responses.py STALE_AFTER)
MAX_ATTEMPTS = 3

MANIFEST_HEAD = """\
# 문서 계획 겸 생성 기록 (7장 "평가용 데이터"). tools/wikigen의 plan 단계가 만든다. 손으로 고치지 않는다.
# 문서마다 핵심 사실(key_facts)과 심은 의도(intents)를 적는다. 골든셋 정답 라벨과 권한 테스트셋의 기준 원장이다.
# intents: revision_old/new, contradiction_old/new(pairs의 옛·새 문서), restricted(문서 제한),
#          empty_restricted·empty_space(빈 권한, 아무도 못 봄), confidential(기밀), injection(지시문 심음),
#          recent(이번 달 변경), stale(1년 넘게 수정 안 됨)
"""


def extract_json(text: str) -> Any:
    """응답에서 JSON을 꺼낸다. COPA가 JSON 모드를 받지 않아 코드 블록이나 대괄호 범위로 찾는다."""
    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    raw = fenced.group(1) if fenced else text[text.find("[") : text.rfind("]") + 1]
    return json.loads(raw)


def allocate(total: int, weights: dict[str, int]) -> dict[str, int]:
    """total을 weights 비율로 나눈다 (최대 나머지 방식). 합이 정확히 total이 된다."""
    weight_sum = sum(weights.values())
    if total <= 0 or weight_sum == 0:
        return dict.fromkeys(weights, 0)
    raw = {k: total * w / weight_sum for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (out[k] - raw[k], k))[: total - sum(out.values())]:
        out[k] += 1
    return out


def is_recent(spec: Spec, d: date) -> bool:
    return (d.year, d.month) == (spec.today.year, spec.today.month)


def is_stale(spec: Spec, d: date) -> bool:
    return (spec.today - d).days > STALE_DAYS


def planner_counts(spec: Spec) -> dict[str, tuple[int, int]]:
    """스페이스마다 LLM이 계획할 문서 수와, 그중 이번 달에 바뀐 문서 수."""
    planted = Counter(p["space"] for p in spec.plants.values())
    todo = {sid: s["docs"] - planted[sid] for sid, s in spec.spaces.items()}
    recent_planted = sum(is_recent(spec, p["updated"]) for p in spec.plants.values())
    recent = allocate(spec.targets["recent_docs"] - recent_planted, todo)
    return {sid: (todo[sid], recent[sid]) for sid in spec.spaces}


def validate_planned(spec: Spec, space_id: str, items: Any, n: int, recent_n: int, taken: list[str]) -> list[str]:
    if not isinstance(items, list):
        return ["JSON 배열이 아니다"]
    problems = []
    if len(items) != n:
        problems.append(f"문서가 {len(items)}개다. 정확히 {n}개여야 한다")
    terms = {g["term"] for g in spec.glossary}
    doc_types = spec.spaces[space_id]["doc_types"]
    seen = set(taken)
    last_month = (spec.today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    facts_all: list[str] = []
    changes = 0
    for i, it in enumerate(items, 1):
        where = f"{i}번 문서"
        if not isinstance(it, dict):
            problems.append(f"{where}: 객체가 아니다")
            continue
        title = it.get("title")
        if not isinstance(title, str) or not 2 <= len(title) <= 60:
            problems.append(f"{where}: title은 2~60자 문자열이어야 한다")
        elif title in seen:
            problems.append(f"{where}: 제목 '{title}'이 다른 문서와 겹친다")
        seen.add(str(title))
        if it.get("doc_type") not in doc_types:
            problems.append(f"{where}: doc_type은 {', '.join(doc_types)} 중 하나여야 한다")
        if not isinstance(it.get("summary"), str) or not it["summary"].strip():
            problems.append(f"{where}: summary가 비었다")
        facts = it.get("key_facts")
        if not isinstance(facts, list) or not 3 <= len(facts) <= 5:
            problems.append(f"{where}: key_facts는 3~5개여야 한다")
            facts = facts if isinstance(facts, list) else []
        musts = []
        for f in facts:
            if not (isinstance(f, dict) and isinstance(f.get("fact"), str) and isinstance(f.get("must_include"), str)):
                problems.append(f"{where}: key_facts 항목은 fact와 must_include 문자열이어야 한다")
                continue
            must = f["must_include"]
            if not 2 <= len(must) <= 30 or must not in f["fact"]:
                problems.append(f"{where}: must_include '{must}'는 fact 안에 글자 그대로 있는 2~30자여야 한다")
            hits = [k for k in spec.absent_keywords if k in f["fact"]]
            if hits:
                problems.append(f"{where}: 위키에 없어야 하는 주제의 말({', '.join(hits)})을 썼다")
            codes = spec.unknown_codes(f["fact"])
            if codes:
                problems.append(f"{where}: 용어 사전에 없는 코드 {', '.join(codes)}를 만들었다")
            musts.append(must)
        if len(set(musts)) != len(musts):
            problems.append(f"{where}: must_include가 서로 겹친다")
        facts_all += [f["fact"] for f in facts if isinstance(f, dict) and isinstance(f.get("fact"), str)]
        used = it.get("terms", [])
        if not isinstance(used, list):
            problems.append(f"{where}: terms는 배열이어야 한다")
        elif unknown := [t for t in used if t not in terms]:
            problems.append(f"{where}: terms의 {', '.join(map(str, unknown))}는 용어 사전에 없다. 사전의 용어만 넣는다")
        change = it.get("change")
        if change is not None and (not isinstance(change, str) or not change.strip()):
            problems.append(f"{where}: change는 문자열이나 null이어야 한다")
        changes += bool(change)
        written = it.get("date")
        if written is not None:
            if not (isinstance(written, str) and re.fullmatch(r"\d{4}-\d{2}", written)
                    and "2023-01" <= written <= last_month):
                problems.append(f"{where}: date는 null이거나 2023-01~{last_month} 사이의 YYYY-MM이어야 한다")
            elif change:
                problems.append(f"{where}: change가 있는 문서는 date를 null로 둔다")
        elif not change:
            text = " ".join([str(title), *(f.get("fact", "") for f in facts if isinstance(f, dict))])
            if re.search(r"20\d\d년", text):
                problems.append(f"{where}: date가 null인데 제목이나 핵심 사실에 연도가 있다. "
                                "특정 시점의 문서면 date를 적고, 아니면 연도를 뺀다")
    if changes != recent_n:
        problems.append(f"change가 있는 문서가 {changes}개다. 정확히 {recent_n}개여야 한다")
    joined = "\n".join(facts_all)
    for g in spec.glossary:
        if g["space"] == space_id and g["term"] not in joined:
            problems.append(f"용어 '{g['term']}'를 설명하는 핵심 사실이 없다. fact 문장에 용어를 그대로 쓴다")
    return problems


def tidy(spec: Spec, space_id: str, items: Any, n: int) -> Any:
    """LLM이 자주 틀리지만 다시 부를 필요는 없는 것을 고친다.

    terms는 본문 작성 때 뜻을 알려 줄 용어 목록일 뿐이라, 사전에 없는 말은 빼면 된다.
    문서를 n개보다 많이 계획했으면 뒤에서부터 덜어 낸다. LLM은 개수를 정확히 맞추지 못할 때가 많다.
    이번 달 변경 문서와, 이 스페이스 용어를 혼자 설명하는 문서는 남긴다.
    """
    if not isinstance(items, list):
        return items
    known = {g["term"] for g in spec.glossary}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("terms"), list):
            item["terms"] = [t for t in item["terms"] if t in known]
        if isinstance(item, dict) and item.get("change") == "":
            item["change"] = None
    if len(items) <= n:
        return items
    own = [g["term"] for g in spec.glossary if g["space"] == space_id]
    kept = list(items)

    def facts_text(docs) -> str:
        return "\n".join(f.get("fact", "") for d in docs if isinstance(d, dict)
                         for f in d.get("key_facts") or [] if isinstance(f, dict))

    for item in reversed(items):
        if len(kept) == n:
            break
        if not isinstance(item, dict) or item.get("change"):
            continue
        rest = [d for d in kept if d is not item]
        if all(t in facts_text(rest) for t in own if t in facts_text(kept)):
            kept = rest
    return kept


def plan_space(llm, spec: Spec, space_id: str, n: int, recent_n: int, taken: list[str], cache: Path) -> list[dict]:
    """한 스페이스의 문서를 계획한다. 검증을 통과한 결과는 캐시에 두고 다시 부르지 않는다."""
    if cache.exists():
        items = json.loads(cache.read_text(encoding="utf-8"))
        if not validate_planned(spec, space_id, items, n, recent_n, taken):
            return items
    messages = plan_messages(spec, space_id, n, recent_n, taken)
    problems: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text, meta = llm.chat(messages, max_tokens=16000)
        try:
            items = tidy(spec, space_id, extract_json(text), n)
            problems = validate_planned(spec, space_id, items, n, recent_n, taken)
        except ValueError as e:  # json.JSONDecodeError도 ValueError다
            items, problems = None, [f"JSON을 읽을 수 없다: {e}"]
        llm.record({"step": "plan", "target": space_id, "attempt": attempt, **meta, "problems": problems})
        if not problems and isinstance(items, list):
            cache.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            return items
        messages = [*messages, {"role": "assistant", "content": text}, retry_message(problems)]
    raise RuntimeError(f"{space_id}: {MAX_ATTEMPTS}번 시도해도 검증을 통과하지 못했다: {problems}")


def build_manifest(spec: Spec, planned: dict[str, list[dict]]) -> dict[str, Any]:
    """심을 문서와 LLM 계획을 합쳐 id, 날짜, 버전, 의도를 정한다. LLM을 부르지 않는다."""
    rng = random.Random(spec.schema["seed"])
    today = spec.today
    month_start = today.replace(day=1)

    def pick(start: date, end: date) -> date:
        return start + timedelta(days=rng.randint(0, (end - start).days))

    # LLM이 계획한 문서의 수정일. change가 있으면 이번 달, date(쓴 달)가 있으면 그달이다.
    # 나머지 가운데 일부를 1년 넘게 전으로 보내 오래된 문서 수를 목표에 맞추고, 그 밖은 그 사이로 둔다
    refs = [(sid, i) for sid in spec.spaces for i in range(len(planned.get(sid, [])))]
    day_of: dict[tuple[str, int], date] = {}
    for r in refs:
        item = planned[r[0]][r[1]]
        if item.get("change"):
            day_of[r] = pick(month_start, today - timedelta(days=1))
        elif item.get("date"):
            first = date.fromisoformat(item["date"] + "-01")
            last = (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)
            day_of[r] = pick(first, min(last, today - timedelta(days=1)))
    quiet = [r for r in refs if r not in day_of]
    need = (spec.targets["stale_docs"] - sum(is_stale(spec, p["updated"]) for p in spec.plants.values())
            - sum(is_stale(spec, d) for d in day_of.values()))
    if not 0 <= need <= len(quiet):
        raise ValueError(f"오래된 문서 목표를 맞출 수 없다 (더 필요한 수 {need}, 후보 {len(quiet)}). "
                         "날짜가 정해진 문서를 줄여 다시 계획한다")
    stale = set(rng.sample(quiet, need))
    for r in quiet:
        if r in stale:
            day_of[r] = pick(today - timedelta(days=3 * 365), today - timedelta(days=STALE_DAYS + 15))
        else:
            day_of[r] = pick(today - timedelta(days=STALE_DAYS - 5), month_start - timedelta(days=1))

    role = {}
    for pair in spec.pairs:
        role[pair["old"]] = (pair, "old")
        role[pair["new"]] = (pair, "new")

    documents, key_to_id = [], {}
    for sid, space in spec.spaces.items():
        entries: list[tuple[str | None, dict, date]] = [
            (key, p, p["updated"]) for key, p in spec.plants.items() if p["space"] == sid
        ]
        entries += [(None, it, day_of[(sid, i)]) for i, it in enumerate(planned.get(sid, []))]
        rng.shuffle(entries)  # 심은 문서가 id 앞쪽에 몰려 id만 보고 알아채지 않게 섞는다
        for n, (key, item, day) in enumerate(entries, 1):
            doc_id = f"{space['prefix']}-{n:03d}"
            if key:
                key_to_id[key] = doc_id
            documents.append(_record(spec, rng, doc_id, sid, key, item, day, role.get(key)))

    pairs = [{**p, "old": key_to_id[p["old"]], "new": key_to_id[p["new"]]} for p in spec.pairs]
    return {"pairs": pairs, "documents": documents}


def _record(spec: Spec, rng: random.Random, doc_id: str, space_id: str, key: str | None, item: dict,
            day: date, pair_role) -> dict[str, Any]:
    space = spec.spaces[space_id]
    restricted = list(item.get("restricted", [ALL]))
    classification = item.get("classification")
    version = rng.randint(1, 6)
    # revision은 내용뿐 아니라 권한·등급이 바뀔 때도 오른다 (ADR-19)
    revision = version + rng.choice([0, 0, 1, 2]) + (restricted != [ALL] or classification is not None)
    at = datetime.combine(day, time(rng.randint(9, 18), rng.randint(0, 59)), KST)

    intents = []
    if pair_role:
        intents.append(f"{pair_role[0]['kind']}_{pair_role[1]}")
    if restricted == []:
        intents.append("empty_restricted")
    elif restricted != [ALL]:
        intents.append("restricted")
    if not space["principals"]:
        intents.append("empty_space")
    if "confidential" in (classification, space["classification"]):
        intents.append("confidential")
    if item.get("injection"):
        intents.append("injection")
    if is_recent(spec, day):
        intents.append("recent")
    if is_stale(spec, day):
        intents.append("stale")

    facts = item.get("facts") or item.get("key_facts") or []
    key_facts = [{"fact": f[0], "must_include": f[1]} if isinstance(f, list) else f for f in facts]
    return {
        "doc_id": doc_id,
        "space": space_id,
        "title": item["title"],
        "doc_type": item["doc_type"],
        "updated_at": at.isoformat(),
        "version": version,
        "revision": revision,
        "restricted": restricted,
        "classification": classification,
        "summary": item["summary"],
        "key_facts": key_facts,
        "terms": list(item.get("terms", [])),
        "change": item.get("change"),
        "injection": item.get("injection"),
        "avoid": list(item.get("avoid", [])),  # 계획 검수에서 정한, 이 문서 본문에 쓰지 않을 내용
        "intents": intents,
        "pair": pair_role[0]["id"] if pair_role else None,
        "plant": key,
        "note": item.get("note"),
    }


def run_plan(spec: Spec, wiki_dir: Path, llm, *, force: bool = False, replan: Sequence[str] = ()) -> Path:
    path = wiki_dir / "manifest.yaml"
    if path.exists() and not force:
        raise SystemExit(f"{path}가 이미 있습니다. 다시 만들려면 --force를 붙이세요 (본문도 다시 써야 합니다).")
    cache_dir = wiki_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    taken = [p["title"] for p in spec.plants.values()]
    planned: dict[str, list[dict]] = {}
    for sid, (n, recent_n) in planner_counts(spec).items():
        if n == 0:
            continue
        cache = cache_dir / f"plan-{sid}.json"
        if sid in replan:
            cache.unlink(missing_ok=True)
        planned[sid] = plan_space(llm, spec, sid, n, recent_n, taken, cache)
        taken += [it["title"] for it in planned[sid]]
        print(f"계획 완료: {sid} {n}개 (이번 달 변경 {recent_n}개)")
    manifest = build_manifest(spec, planned)
    body = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=1000)
    path.write_text(MANIFEST_HEAD + body, encoding="utf-8")
    return path


def load_manifest(wiki_dir: Path) -> dict[str, Any]:
    return yaml.safe_load((wiki_dir / "manifest.yaml").read_text(encoding="utf-8"))
