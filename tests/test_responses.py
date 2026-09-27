from datetime import UTC, datetime

from wiki_rag_mcp.auth.tiers import ClientTier, content_allowed
from wiki_rag_mcp.server.responses import (
    CONFIDENTIAL_NOTE,
    RESPONSE_SNIPPET_CHARS,
    SNIPPET_CHARS,
    STALE_NOTE,
    UNTRUSTED_NOTICE,
    search_response,
)

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def hit(doc_id="d1", text="본문", classification="general", updated_at="2026-09-01T00:00:00+00:00", score=0.9):
    return {"doc_id": doc_id, "title": "제목", "section_path": ["신청", "방법"], "text": text,
            "url": f"https://wiki/{doc_id}", "version": 3, "updated_at": updated_at,
            "classification": classification, "score": score}


def test_result_fields_and_notice():
    res = search_response([hit()], ClientTier.EXTERNAL, NOW)
    assert res["notice"] == UNTRUSTED_NOTICE
    r = res["results"][0]
    assert set(r) == {"doc_id", "title", "section", "snippet", "url", "version", "updated_at", "score", "notes"}
    assert r["section"] == "신청 > 방법" and r["notes"] == []


def test_snippet_is_capped():
    r = search_response([hit(text="가" * 2000)], ClientTier.EXTERNAL, NOW)["results"][0]
    assert len(r["snippet"]) == SNIPPET_CHARS


def test_confidential_gives_title_and_link_only_to_external_client():
    r = search_response([hit(classification="confidential", text="연봉 인상률 8%")], ClientTier.EXTERNAL, NOW)
    item = r["results"][0]
    assert item["snippet"] == "" and CONFIDENTIAL_NOTE in item["notes"]
    assert item["title"] == "제목" and item["url"]


def test_confidential_content_goes_to_internal_client():
    r = search_response([hit(classification="confidential", text="연봉 인상률 8%")], ClientTier.INTERNAL, NOW)
    assert r["results"][0]["snippet"] == "연봉 인상률 8%"


def test_unknown_classification_is_treated_as_confidential():
    assert content_allowed("secret", ClientTier.EXTERNAL) is False
    assert content_allowed("secret", ClientTier.INTERNAL) is True


def test_stale_document_is_marked():
    r = search_response([hit(updated_at="2024-03-15T10:00:00+09:00")], ClientTier.EXTERNAL, NOW)
    assert STALE_NOTE in r["results"][0]["notes"]


def test_response_budget_stops_adding_results():
    hits = [hit(doc_id=f"d{i}", text="나" * SNIPPET_CHARS) for i in range(10)]
    res = search_response(hits, ClientTier.EXTERNAL, NOW)
    used = sum(len(r["snippet"]) for r in res["results"])
    assert used <= RESPONSE_SNIPPET_CHARS and len(res["results"]) == RESPONSE_SNIPPET_CHARS // SNIPPET_CHARS
