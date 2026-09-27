"""골든셋 만들기의 LLM을 부르지 않는 부분: 출처 선택과 질문 검증."""

from collections import Counter
from pathlib import Path

import pytest
import yaml

from tools.goldset.select import QUOTAS, select
from tools.goldset.write import check, copied
from tools.wikigen.spec import load_spec

ROOT = Path(__file__).parent.parent
WIKI = ROOT / "data" / "wiki"


@pytest.fixture(scope="module")
def picked():
    spec = load_spec(WIKI / "schema.yaml", ROOT / "tools" / "wikigen" / "plants.yaml")
    manifest = yaml.safe_load((WIKI / "manifest.yaml").read_text(encoding="utf-8"))
    return spec, manifest, select(spec, manifest)


def test_quotas_and_determinism(picked):
    spec, manifest, sources = picked
    assert Counter(s["type"] for s in sources) == Counter(QUOTAS)
    assert sources == select(spec, manifest)
    assert len({s["id"] for s in sources}) == 300


def test_every_asker_can_see_the_source_doc(picked):
    spec, manifest, sources = picked
    docs = {d["doc_id"]: d for d in manifest["documents"]}
    for s in sources:
        if s["doc_id"]:
            d = docs[s["doc_id"]]
            assert spec.can_see(s["asker"], spec.spaces[d["space"]]["principals"], d["restricted"]), s["id"]


def test_contradiction_questions_point_at_the_newer_doc(picked):
    _, manifest, sources = picked
    new_docs = {p["new"] for p in manifest["pairs"]}
    assert {s["doc_id"] for s in sources if s["type"] == "contradiction"} == new_docs


def test_copied_ignores_spaces_and_allowed_term():
    fact = "연차는 사용 예정일 3영업일 전까지 인사 시스템에서 신청한다"
    assert copied("연차 신청은 며칠 전에 해야 해?", fact) is None
    assert copied("인사 시스템에서 신청하는 거 맞아?", "연차는 인사 시스템에서 신청한다") == "인사시스템에서신청"
    assert copied("SP-E2003 에러는 뭐야?", "SP-E2003 에러는 정산 금액 불일치다", keep="SP-E2003") is None


def test_check_rejects_leaked_answers_and_missing_terms():
    batch = [{"id": "q1", "type": "fact", "fact": "연차는 3영업일 전까지 신청한다", "must_include": "3영업일"},
             {"id": "q2", "type": "abbreviation", "fact": "누리는 정산 배치다", "must_include": "정산 배치",
              "term": "누리"}]
    ok, problems = check(batch, [{"id": "q1", "question": "연차 며칠 전에 올려야 돼?"},
                                 {"id": "q2", "question": "누리가 뭐 하는 시스템이야?"}])
    assert problems == [] and set(ok) == {"q1", "q2"}
    _, problems = check(batch, [{"id": "q1", "question": "연차 3영업일 전에 내면 돼?"},
                                {"id": "q2", "question": "정산 돌리는 배치 이름이 뭐야?"}])
    assert any("3영업일" in p for p in problems) and any("누리" in p for p in problems)
