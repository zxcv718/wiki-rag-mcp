from wiki_rag_mcp.indexing.chunker import (
    Section,
    chunk_document,
    context_header,
    split_blocks,
    split_sections,
)


def words(text: str) -> int:
    """테스트에서는 모델 없이 단어 수를 토큰 수로 쓴다."""
    return len(text.split())


DOC = """# 운영 DB 접근 권한

운영 DB 권한 신청 전에 읽어 주세요.

## 신청 방법

보안팀 양식으로 신청합니다.

### 조회 권한

조회 권한은 최대 30일입니다.

```bash
# 이 줄은 제목이 아니다
psql -h prod-db
```

## 만료

만료 전에 연장 신청을 합니다.
"""


def test_sections_follow_heading_path_and_skip_title_h1():
    sections = split_sections(DOC, "운영 DB 접근 권한")
    assert [s.path for s in sections] == [(), ("신청 방법",), ("신청 방법", "조회 권한"), ("만료",)]


def test_hash_inside_code_fence_is_not_a_heading():
    sections = split_sections(DOC, "운영 DB 접근 권한")
    assert "# 이 줄은 제목이 아니다" in sections[2].text


def test_blocks_keep_tables_and_code_whole():
    text = "앞 문단.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n뒤 문단.\n\n```py\nx = 1\n\ny = 2\n```"
    kinds = [b.kind for b in split_blocks(text)]
    assert kinds == ["text", "table", "text", "code"]
    assert split_blocks(text)[3].text.count("\n") == 4  # 빈 줄이 있어도 코드 블록은 하나


def test_long_table_is_never_split():
    rows = "\n".join(f"| 항목{i} | 값{i} | 설명 {'가 ' * 20}|" for i in range(40))
    doc = f"# 제목\n\n## 표\n\n| 항목 | 값 | 설명 |\n|---|---|---|\n{rows}\n"
    chunks = chunk_document("d1", doc, "제목", words, max_tokens=100)
    tables = [c for c in chunks if "| 항목0 |" in c.text]
    assert len(tables) == 1 and "| 항목39 |" in tables[0].text


def test_long_section_is_packed_under_max_with_overlap():
    sentences = " ".join(f"문장 {i} 은 연차 규정의 세부 내용을 설명합니다." for i in range(60))
    doc = f"# 제목\n\n## 연차\n\n{sentences}\n"
    chunks = chunk_document("d1", doc, "제목", words, max_tokens=100, overlap_ratio=0.12)
    assert len(chunks) > 3
    assert all(words(c.text) <= 100 for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        last_sentence = prev.text.rsplit("문장 ", 1)[1]
        assert nxt.text.startswith("문장 " + last_sentence)  # 앞 청크의 끝 문장이 겹친다
    assert {c.part for c in chunks} == set(range(len(chunks)))


def test_section_hash_changes_only_for_the_edited_section():
    before = {c.section_path: c.section_hash for c in chunk_document("d1", DOC, "운영 DB 접근 권한", words)}
    edited = DOC.replace("최대 30일", "최대 14일")
    after = {c.section_path: c.section_hash for c in chunk_document("d1", edited, "운영 DB 접근 권한", words)}
    changed = {p for p in before if before[p] != after[p]}
    assert changed == {("신청 방법", "조회 권한")}


def test_chunk_id_is_stable_when_an_earlier_section_changes():
    before = [c.chunk_id for c in chunk_document("d1", DOC, "운영 DB 접근 권한", words)]
    edited = DOC.replace("보안팀 양식으로 신청합니다.", "보안팀 양식으로 신청하고 팀장 승인을 받습니다.")
    after = [c.chunk_id for c in chunk_document("d1", edited, "운영 DB 접근 권한", words)]
    assert before[-1] == after[-1]  # 뒤 섹션('만료')의 id는 그대로
    assert before[1] != after[1]  # 고친 섹션의 id만 바뀐다


def test_section_hash_includes_path():
    assert Section(("가",), "본문").hash != Section(("나",), "본문").hash


def test_context_header():
    header = context_header("개발", "운영 DB 접근 권한", ("신청 방법", "조회 권한"))
    assert header == "개발 > 운영 DB 접근 권한 > 신청 방법 > 조회 권한"
