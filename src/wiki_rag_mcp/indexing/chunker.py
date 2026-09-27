"""Markdown 문서를 섹션(제목) 기준으로 나눠 청크를 만든다 (ADR-10, 6장 청크 전략).

- 청크는 섹션 경계를 넘지 않는다. 수정은 대개 한 섹션 안에서 일어나서, 경계를 문서 구조에 고정해야
  섹션 해시 비교로 바뀐 청크만 다시 임베딩할 수 있다.
- 긴 섹션은 최대 토큰 수 안에서 블록 단위로 채우고, 다음 청크 앞에 이전 청크 끝의 문장을 조금 겹친다.
- 표와 코드 블록은 자르지 않는다. 반쪽짜리 표는 검색돼도 쓸모가 없다.
  최대 토큰 수를 넘으면 그 블록 하나로 청크를 만든다.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

from wiki_rag_mcp.models import Chunk

CountTokens = Callable[[str], int]

MAX_TOKENS = 500
OVERLAP_RATIO = 0.12  # 설계서의 10~15% 겹침

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|")
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")


@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]
    text: str

    @property
    def hash(self) -> str:
        raw = " > ".join(self.path) + "\n" + self.text
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Block:
    kind: str  # "text", "table", "code"
    text: str

    @property
    def splittable(self) -> bool:
        return self.kind == "text"


def split_sections(markdown: str, doc_title: str) -> list[Section]:
    """제목으로 섹션을 나눈다. 코드 블록 안의 '#'는 제목으로 보지 않는다.

    본문 첫 H1이 문서 제목과 같으면 경로에 넣지 않는다. 섹션 경로는 문서 제목 아래의 경로다.
    """
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []
    in_fence = False

    def flush():
        text = "\n".join(lines).strip("\n")
        if text.strip():
            sections.append(Section(tuple(title for _, title in stack), text))
        lines.clear()

    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if not heading:
            lines.append(line)
            continue
        flush()
        level, title = len(heading.group(1)), heading.group(2).strip()
        if level == 1 and not stack and title == doc_title.strip():
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    flush()
    return sections


def split_blocks(text: str) -> list[Block]:
    """섹션 본문을 문단, 표, 코드 블록으로 나눈다."""
    blocks: list[Block] = []
    buf: list[str] = []
    kind = None

    def flush():
        nonlocal kind
        body = "\n".join(buf).strip("\n")
        if body.strip():
            blocks.append(Block(kind or "text", body))
        buf.clear()
        kind = None

    for line in text.splitlines():
        if kind == "code":
            buf.append(line)
            if _FENCE.match(line):
                flush()
            continue
        if _FENCE.match(line):
            flush()
            kind = "code"
            buf.append(line)
            continue
        is_row = bool(_TABLE_ROW.match(line))
        if kind == "table" and not is_row:
            flush()
        if is_row and kind != "table":
            flush()
            kind = "table"
        if not line.strip() and kind != "table":
            flush()
            continue
        buf.append(line)
    flush()
    return blocks


def _split_long_text(block: Block, count: CountTokens, max_tokens: int) -> list[Block]:
    """최대 토큰 수를 넘는 문단을 문장 단위로 나눈다. 문장 하나가 넘으면 그대로 둔다."""
    if not block.splittable or count(block.text) <= max_tokens:
        return [block]
    parts, current = [], ""
    for sentence in _SENTENCE_END.split(block.text):
        candidate = f"{current} {sentence}".strip()
        if current and count(candidate) > max_tokens:
            parts.append(Block("text", current))
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(Block("text", current))
    return parts


def _overlap(blocks: list[Block], count: CountTokens, budget: int) -> Block | None:
    """이전 청크 끝의 문장을 budget 토큰 안에서 가져온다. 마지막 블록이 표나 코드면 겹치지 않는다."""
    if not blocks or not blocks[-1].splittable or budget <= 0:
        return None
    taken: list[str] = []
    for sentence in reversed(_SENTENCE_END.split(blocks[-1].text)):
        if count(" ".join([sentence, *taken])) > budget:
            break
        taken.insert(0, sentence)
    return Block("text", " ".join(taken)) if taken else None


def _pack(blocks: list[Block], count: CountTokens, max_tokens: int, overlap_tokens: int) -> list[str]:
    """블록을 최대 토큰 수 안에서 순서대로 채워 청크 본문을 만든다."""
    chunks: list[list[Block]] = []
    current: list[Block] = []
    for block in blocks:
        joined = "\n\n".join(b.text for b in [*current, block])
        if current and count(joined) > max_tokens:
            chunks.append(current)
            carry = _overlap(current, count, overlap_tokens)
            current = [carry] if carry else []
            # 겹친 문장을 더해 새 블록이 들어가지 않으면 겹침을 포기한다
            if current and count("\n\n".join(b.text for b in [*current, block])) > max_tokens:
                current = []
        current.append(block)
    if current:
        chunks.append(current)
    return ["\n\n".join(b.text for b in c) for c in chunks]


def chunk_document(doc_id: str, markdown: str, doc_title: str, count: CountTokens,
                   max_tokens: int = MAX_TOKENS, overlap_ratio: float = OVERLAP_RATIO) -> list[Chunk]:
    chunks: list[Chunk] = []
    overlap_tokens = int(max_tokens * overlap_ratio)
    # 긴 문단은 겹칠 문장이 들어갈 자리를 남기고 나눈다. 꽉 채워 나누면 겹침이 매번 한도를 넘어 버려진다
    piece_tokens = max_tokens - overlap_tokens
    for section in split_sections(markdown, doc_title):
        blocks = [part for b in split_blocks(section.text) for part in _split_long_text(b, count, piece_tokens)]
        for part, text in enumerate(_pack(blocks, count, max_tokens, overlap_tokens)):
            chunks.append(Chunk(doc_id=doc_id, chunk_index=len(chunks), section_path=section.path, text=text,
                                section_hash=section.hash, part=part))
    return chunks


def context_header(space_name: str, doc_title: str, section_path: tuple[str, ...]) -> str:
    """임베딩할 때 청크 앞에 붙이는 맥락 헤더. "설정 방법"처럼 흔한 섹션이 어느 문서의 것인지 잃지 않게 한다."""
    return " > ".join([space_name, doc_title, *section_path])
