"""experiments/m6-tool-scope의 B안 구성과 판정 규칙."""

from wiki_rag_mcp.agent.scope import decide, definitions

BASE = {
    "instructions": "사내 위키 검색 서버입니다.",
    "tools": [{"type": "function", "function": {"name": n, "description": f"{n} 설명", "parameters": {}}}
              for n in ("search_wiki", "get_document", "list_recent_changes")],
}


def test_variant_b_changes_only_the_instructions_and_the_search_description():
    instructions, tools = definitions(BASE, {"instructions": "새 안내", "search_wiki": "새 설명"})
    assert instructions == "새 안내"
    assert [t["function"]["description"] for t in tools] == ["새 설명", "get_document 설명", "list_recent_changes 설명"]
    assert BASE["tools"][0]["function"]["description"] == "search_wiki 설명"  # A는 그대로
    assert definitions(BASE, None) == (BASE["instructions"], BASE["tools"])


def _runs(condition: str, company_hits: int, general_calls: int, reps: int = 3) -> list[dict]:
    """회사 질문 24개 중 company_hits개가 늘 위키를 찾고, 일반 질문 16개 중 general_calls개가 늘 부른다."""
    records = []
    for rep in range(reps):
        for i in range(24):
            chosen = ["search_wiki"] if i < company_hits else []
            records.append({"id": f"c{i}", "expected": "wiki", "condition": condition, "rep": rep, "chosen": chosen})
        for i in range(16):
            chosen = ["search_wiki"] if i < general_calls else []
            records.append({"id": f"g{i}", "expected": "none", "condition": condition, "rep": rep, "chosen": chosen})
    return records


def _regression(list_errors: int) -> list[dict]:
    rows = [("list_recent_changes", 20, list_errors), ("get_document", 10, 0), ("none", 20, 0)]
    records = []
    for expected, n, wrong in rows:
        for i in range(n):
            right = [] if expected == "none" else [expected]
            chosen = ["search_wiki"] if i < wrong else right
            records.append({"id": f"{expected}{i}", "expected": expected, "condition": "B", "rep": 0, "chosen": chosen})
    return records


def test_fewer_misses_without_more_overcalls_is_adopted():
    d = decide(_runs("A", 16, 0) + _runs("B", 24, 0), _regression(0))
    assert d["company_verdict"] == "채택" and d["final"] == "채택"


def test_more_overcalls_reject_even_when_misses_drop():
    d = decide(_runs("A", 16, 0) + _runs("B", 24, 3), _regression(0))  # 과잉 호출 3/16 = 19%
    assert not d["general_ok"] and d["final"] == "기각"


def test_demo_agent_regression_rejects():
    d = decide(_runs("A", 16, 0) + _runs("B", 24, 0), _regression(3))  # list_recent_changes 오류 15%
    assert not d["regression_ok"] and d["final"] == "기각"


def test_no_change_is_rejected_and_a_small_gain_is_held():
    assert decide(_runs("A", 24, 0) + _runs("B", 24, 0), _regression(0))["final"] == "기각"
    d = decide(_runs("A", 22, 0) + _runs("B", 24, 0), _regression(0))  # 차이 8%p, 구간이 기준값에 걸침
    assert d["company_verdict"] == "보류" and d["final"] == "보류"
