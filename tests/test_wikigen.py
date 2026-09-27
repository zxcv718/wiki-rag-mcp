"""가상 위키 생성기(tools/wikigen)의 LLM을 부르지 않는 부분: 계획 검증, 날짜·id 배정, 본문 검증, 점검."""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from tools.wikigen.lint import lint
from tools.wikigen.plan import (
    MANIFEST_HEAD,
    allocate,
    build_manifest,
    extract_json,
    is_recent,
    is_stale,
    planner_counts,
    tidy,
    validate_planned,
)
from tools.wikigen.spec import load_spec
from tools.wikigen.write import check_body, front_matter, normalize, write_doc

ROOT = Path(__file__).parent.parent
SCHEMA = ROOT / "data" / "wiki" / "schema.yaml"
PLANTS = ROOT / "tools" / "wikigen" / "plants.yaml"


@pytest.fixture(scope="module")
def spec():
    return load_spec(SCHEMA, PLANTS)


def fake_plan(spec) -> dict[str, list[dict]]:
    """LLM 대신 규칙대로 만든 계획. 스페이스의 용어는 그 스페이스 문서에 골고루 나눠 넣는다."""
    planned = {}
    for sid, (n, recent_n) in planner_counts(spec).items():
        own = [g["term"] for g in spec.glossary if g["space"] == sid]
        items = []
        for i in range(n):
            terms = own[i::n] if n else []
            facts = [{"fact": f"{sid} 문서 {i}의 기준은 {sid}-{i}-{k}이다", "must_include": f"{sid}-{i}-{k}"}
                     for k in range(3)]
            facts += [{"fact": f"{t}을 쓴다", "must_include": t} for t in terms]
            items.append({"title": f"{sid} 문서 {i}", "doc_type": spec.spaces[sid]["doc_types"][0],
                          "summary": "요약", "key_facts": facts, "terms": terms,
                          "change": "기준을 바꿨다" if i < recent_n else None,
                          "date": "2026-03" if i == recent_n else None})
        planned[sid] = items
    return planned


def fake_body(doc: dict) -> str:
    """계획을 모두 지킨 본문."""
    lines = [f"# {doc['title']}", "", "## 개요", "", doc["summary"] + " 내용을 설명합니다." * 60, "", "## 기준", ""]
    lines += [f"- {f['fact']}" for f in doc["key_facts"]]
    lines += ["", "용어: " + ", ".join(doc["terms"])] if doc["terms"] else []
    lines += ["", doc["injection"]] if doc.get("injection") else []
    if doc.get("change"):
        lines += ["", "## 변경 이력", "", "| 날짜 | 내용 |", "|---|---|"]
        lines.append(f"| {doc['updated_at'][:10]} | {doc['change']} |")
    return "\n".join(lines) + "\n"


def write_wiki(tmp_path: Path, manifest: dict) -> Path:
    wiki = tmp_path / "wiki"
    (wiki / "docs").mkdir(parents=True)
    shutil.copy(SCHEMA, wiki / "schema.yaml")
    (wiki / "manifest.yaml").write_text(MANIFEST_HEAD + yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
                                        encoding="utf-8")
    for doc in manifest["documents"]:
        (wiki / "docs" / f"{doc['doc_id']}.md").write_text(front_matter(doc) + "\n" + fake_body(doc), encoding="utf-8")
    return wiki


def test_schema_and_plants_add_up_to_200_documents(spec):
    counts = planner_counts(spec)
    assert sum(n for n, _ in counts.values()) + len(spec.plants) == 200
    recent_planted = sum(is_recent(spec, p["updated"]) for p in spec.plants.values())
    assert sum(r for _, r in counts.values()) + recent_planted == spec.targets["recent_docs"]
    assert all(r <= n for n, r in counts.values())


def test_allocate_keeps_the_total():
    assert sum(allocate(21, {"a": 9, "b": 17, "c": 22, "d": 0}).values()) == 21
    assert allocate(0, {"a": 1}) == {"a": 0}


def test_extract_json_from_fenced_or_bare_answer():
    assert extract_json('계획입니다.\n```json\n[{"a": 1}]\n```\n') == [{"a": 1}]
    assert extract_json('앞말 [{"a": 2}] 뒷말') == [{"a": 2}]


def test_validate_planned_reports_what_to_fix(spec):
    good = fake_plan(spec)["data"]
    n, recent_n = planner_counts(spec)["data"]
    assert validate_planned(spec, "data", good, n, recent_n, []) == []

    bad = json.loads(json.dumps(good))
    bad[0]["key_facts"][0]["must_include"] = "본문에 없는 말"
    bad[1]["terms"] = ["사전에 없는 용어"]
    bad[2]["change"] = "하나 더 바꿨다"
    bad[4]["date"] = "2026-09"
    bad[5]["title"] = "2025년 정산 점검 결과"
    problems = validate_planned(spec, "data", bad[:-1], n, recent_n, [good[3]["title"]])
    joined = "\n".join(problems)
    expected_all = ["정확히", "fact 안에", "용어 사전에 없다", "change가 있는 문서", "겹친다", "YYYY-MM", "연도가 있다"]
    for expected in expected_all:
        assert expected in joined


def test_tidy_trims_extra_docs_but_keeps_changed_docs_and_terms(spec):
    n, recent_n = planner_counts(spec)["data"]
    items = fake_plan(spec)["data"]
    extra = [{**items[-1], "title": "추가 문서", "change": None}, {**items[0], "title": "변경 추가"}]
    trimmed = tidy(spec, "data", [*items[:1], extra[1], *items[1:], extra[0]], n + 1)
    assert len(trimmed) == n + 1 and extra[1] in trimmed  # change가 있는 문서는 덜지 않는다
    assert validate_planned(spec, "data", tidy(spec, "data", [*items, extra[0]], n), n, recent_n, []) == []
    assert tidy(spec, "data", [{**items[0], "terms": ["별빛", "Kubernetes"]}], n)[0]["terms"] == ["별빛"]


def test_manifest_is_deterministic_and_meets_targets(spec):
    planned = fake_plan(spec)
    manifest = build_manifest(spec, planned)
    assert manifest == build_manifest(spec, planned)

    docs = manifest["documents"]
    assert len(docs) == 200 and len({d["doc_id"] for d in docs}) == 200
    days = [d["updated_at"][:10] for d in docs]
    assert sum(is_recent(spec, date.fromisoformat(x)) for x in days) == spec.targets["recent_docs"]
    assert sum(is_stale(spec, date.fromisoformat(x)) for x in days) == spec.targets["stale_docs"]
    by_id = {d["doc_id"]: d for d in docs}
    for pair in manifest["pairs"]:
        assert by_id[pair["old"]]["updated_at"] < by_id[pair["new"]]["updated_at"]
        assert f"{pair['kind']}_old" in by_id[pair["old"]]["intents"]
    dated = {f"{sid} 문서 {recent_n}" for sid, (n, recent_n) in planner_counts(spec).items() if n > recent_n}
    assert all(d["updated_at"].startswith("2026-03") for d in docs if d["title"] in dated)
    dba = next(d for d in docs if d["plant"] == "infra-db-password")
    assert dba["restricted"] == ["group:dba"] and "restricted" in dba["intents"]


def test_check_body_catches_rule_breaks(spec):
    doc = next(d for d in build_manifest(spec, fake_plan(spec))["documents"] if d["plant"] == "eng-oss-intake")
    assert check_body(doc, fake_body(doc), spec) == []

    broken = fake_body(doc).replace(doc["key_facts"][0]["must_include"], "").replace(doc["injection"], "")
    broken += "\n사내 어린이집은 1층에 있습니다 — 참고하세요. SP-E9999도 확인합니다.\n"
    broken += "다른 용어는 이미 아는 말처럼 씁니다.\n"
    joined = "\n".join(check_body(doc, broken, spec))
    for expected in ["글자 그대로 없다", "심어야 할 문장", "em dash", "어린이집", "SP-E9999", "작성 규칙"]:
        assert expected in joined


def test_normalize_unwraps_code_fence_and_fixes_title():
    assert normalize("```markdown\n# 다른 제목\n\n## 절\n본문\n```", "원래 제목") == "# 원래 제목\n\n## 절\n본문\n"


class FakeLLM:
    def __init__(self, answers):
        self.answers = list(answers)
        self.log = []

    def chat(self, _messages, **_):
        return self.answers.pop(0), {"finish_reason": "stop", "prompt_tokens": 1, "completion_tokens": 1}

    def record(self, entry):
        self.log.append(entry)


def test_write_doc_retries_with_feedback_until_valid(spec, tmp_path):
    doc = next(d for d in build_manifest(spec, fake_plan(spec))["documents"] if d["plant"] == "hr-remote")
    missing = fake_body(doc).replace("주 3회", "주 세 번")
    llm = FakeLLM([missing, fake_body(doc)])
    problems, attempts = write_doc(llm, spec, doc, [], tmp_path / "doc.md")
    assert problems == [] and attempts == 2
    assert llm.log[0]["problems"] and llm.log[1]["problems"] == []
    assert (tmp_path / "doc.md").read_text(encoding="utf-8").startswith("---\ndoc_id: ")


def test_lint_passes_on_consistent_wiki_and_catches_drift(spec, tmp_path):
    manifest = build_manifest(spec, fake_plan(spec))
    wiki = write_wiki(tmp_path, manifest)
    problems, stats = lint(spec, wiki)
    assert problems == []
    assert stats["문서"] == 200 and stats["개정 전후 쌍"] + stats["모순 쌍"] == 15

    # 문서 한 개의 권한을 손으로 넓히고, 다른 한 개는 핵심 사실을 지운다
    dba = next(d for d in manifest["documents"] if d["plant"] == "infra-db-password")
    path = wiki / "docs" / f"{dba['doc_id']}.md"
    path.write_text(path.read_text(encoding="utf-8").replace('restricted: ["group:dba"]\n', ""), encoding="utf-8")
    other = manifest["documents"][0]
    path = wiki / "docs" / f"{other['doc_id']}.md"
    path.write_text(path.read_text(encoding="utf-8").replace(other["key_facts"][0]["must_include"], "?"),
                    encoding="utf-8")
    joined = "\n".join(lint(spec, wiki)[0])
    assert f"{dba['doc_id']}: restricted_principals" in joined
    assert f"{other['doc_id']}: 핵심 사실" in joined
