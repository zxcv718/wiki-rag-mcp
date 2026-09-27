"""3단계: 점검. 생성 기록(manifest)과 실제 문서가 맞는지, 심은 사례가 의도대로 들어갔는지 확인한다.

LLM을 부르지 않으므로 CI에서도 돌린다. 서버가 쓰는 FileWikiSource로 문서를 읽어, 서버가 보는 권한과
등급이 계획과 같은지도 함께 확인한다.
"""

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.wikigen.plan import is_recent, is_stale, load_manifest
from tools.wikigen.spec import Spec
from tools.wikigen.write import check_body
from wiki_rag_mcp.models import Document
from wiki_rag_mcp.wiki.files import FileWikiSource


def _front_matter_problems(spec: Spec, plan: dict[str, Any], doc: Document) -> list[str]:
    space_level = spec.spaces[plan["space"]]["classification"]
    expected = {
        "title": plan["title"],
        "space": plan["space"],
        "version": plan["version"],
        "revision": plan["revision"],
        "updated_at": datetime.fromisoformat(plan["updated_at"]),
        "restricted_principals": tuple(plan["restricted"]),
        "space_principals": tuple(spec.spaces[plan["space"]]["principals"]),
        "classification": "confidential" if "confidential" in (plan["classification"], space_level) else "general",
    }
    return [f"{field}이 계획과 다르다 ({getattr(doc, field)!r} != {value!r})"
            for field, value in expected.items() if getattr(doc, field) != value]


def _permission_problems(spec: Spec, plans: dict[str, dict], docs: dict[str, Document]) -> list[str]:
    problems = []
    users = list(spec.users)

    def viewers(d: Document) -> list[str]:
        return [u for u in users if spec.can_see(u, d.space_principals, d.restricted_principals)]

    for doc_id, d in docs.items():
        intents = plans[doc_id]["intents"]
        if {"empty_restricted", "empty_space"} & set(intents):
            if viewers(d):
                problems.append(f"{doc_id}: 빈 권한 문서인데 {viewers(d)}가 볼 수 있다")
        elif not viewers(d):
            problems.append(f"{doc_id}: 볼 수 있는 가상 사용자가 없다")
    restricted = [d for i, d in docs.items() if "restricted" in plans[i]["intents"]]
    in_space_only = any(spec.can_see(u, d.space_principals, ["all"]) and not spec.can_see(u, ["all"],
                        d.restricted_principals) for d in restricted for u in users)
    in_restriction_only = any(spec.can_see(u, ["all"], d.restricted_principals) and not spec.can_see(
                              u, d.space_principals, ["all"]) for d in restricted for u in users)
    if not in_space_only:
        problems.append("스페이스 멤버이지만 제한 대상이 아닌 사용자 사례가 없다 (ADR-21)")
    if not in_restriction_only:
        problems.append("제한 대상이지만 스페이스 멤버가 아닌 사용자 사례가 없다 (ADR-21)")
    blind = [u for u in users if not any(u in viewers(d) for d in docs.values())]
    problems += [f"사용자 {u}: 볼 수 있는 문서가 없다" for u in blind]
    return problems


def lint(spec: Spec, wiki_dir: Path) -> tuple[list[str], dict[str, Any]]:
    manifest = load_manifest(wiki_dir)
    plans = {d["doc_id"]: d for d in manifest["documents"]}
    files = {p.stem for p in (wiki_dir / "docs").glob("*.md")}
    problems = [f"{i}: 계획에는 있지만 파일이 없다" for i in sorted(plans.keys() - files)]
    problems += [f"{i}: 계획에 없는 파일이다" for i in sorted(files - plans.keys())]
    try:
        source = FileWikiSource(wiki_dir)
    except ValueError as e:
        return [*problems, f"서버가 위키를 읽지 못한다: {e}"], {}
    docs = {d.doc_id: d for d in source.documents() if d.doc_id in plans}

    for doc_id, d in docs.items():
        problems += [f"{doc_id}: {p}" for p in _front_matter_problems(spec, plans[doc_id], d)]
        problems += [f"{doc_id}: {p}" for p in check_body(plans[doc_id], d.body, spec)]

    titles = Counter(p["title"] for p in plans.values())
    problems += [f"제목이 겹친다: {t}" for t, n in titles.items() if n > 1]
    per_space = Counter(p["space"] for p in plans.values())
    problems += [f"스페이스 {sid}: 문서 {per_space[sid]}개, 스키마는 {s['docs']}개"
                 for sid, s in spec.spaces.items() if per_space[sid] != s["docs"]]

    days = {i: datetime.fromisoformat(p["updated_at"]).date() for i, p in plans.items()}
    recent = sum(is_recent(spec, d) for d in days.values())
    stale = sum(is_stale(spec, d) for d in days.values())
    if recent != spec.targets["recent_docs"]:
        problems.append(f"이번 달 수정 문서가 {recent}개다. 목표는 {spec.targets['recent_docs']}개")
    if stale != spec.targets["stale_docs"]:
        problems.append(f"오래된 문서가 {stale}개다. 목표는 {spec.targets['stale_docs']}개")

    for pair in manifest["pairs"]:
        if days[pair["old"]] >= days[pair["new"]]:
            problems.append(f"쌍 {pair['id']}: 옛 문서가 새 문서보다 늦게 수정됐다")

    bodies = {i: d.body for i, d in docs.items()}
    for g in spec.glossary:
        home = [i for i, b in bodies.items() if plans[i]["space"] == g["space"] and g["term"] in b]
        if not home:
            problems.append(f"용어 {g['term']}: 설명할 스페이스 {g['space']}의 문서에 나오지 않는다")

    problems += _permission_problems(spec, plans, docs)
    return problems, _stats(spec, manifest, bodies)


def _stats(spec: Spec, manifest: dict[str, Any], bodies: dict[str, str]) -> dict[str, Any]:
    intents = Counter(i for d in manifest["documents"] for i in d["intents"])
    kinds = Counter(p["kind"] for p in manifest["pairs"])
    chars = [len(b) for b in bodies.values()]
    return {
        "문서": len(manifest["documents"]),
        "스페이스": len(spec.spaces),
        "가상 사용자": len(spec.users),
        "개정 전후 쌍": kinds["revision"],
        "모순 쌍": kinds["contradiction"],
        "제한 문서": intents["restricted"],
        "빈 권한 문서": intents["empty_restricted"] + intents["empty_space"],
        "기밀 문서": intents["confidential"],
        "지시문을 심은 문서": intents["injection"],
        "이번 달 수정 문서": intents["recent"],
        "오래된 문서": intents["stale"],
        "약어·고유명사": len(spec.glossary),
        "없어야 할 주제": len(spec.schema["absent_topics"]),
        "본문 평균 글자 수": round(sum(chars) / len(chars)) if chars else 0,
    }
