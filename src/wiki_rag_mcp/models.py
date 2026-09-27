"""문서와 청크의 공통 데이터 구조."""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# 위키 서비스와 같은 id 형식(wiki-service/README.md). 문서·사용자 id는 위키 API의 URL 경로에 들어가므로, 점으로만
# 된 id(".", "..")는 경로 이동으로 해석되지 않게 막는다. fullmatch로 써야 끝의 줄바꿈까지 거부한다
WIKI_ID = re.compile(r"(?!\.+\Z)[A-Za-z0-9._-]{1,64}")


def valid_doc_id(doc_id: str) -> bool:
    return WIKI_ID.fullmatch(doc_id) is not None


def valid_user_id(user_id: str) -> bool:
    return WIKI_ID.fullmatch(user_id) is not None


class Classification(StrEnum):
    """문서 등급 (ADR-17). 순서가 곧 높낮이다."""

    GENERAL = "general"
    CONFIDENTIAL = "confidential"


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    space: str
    url: str
    version: int  # 내용 버전. 응답의 출처 표기에 쓴다
    revision: int  # 내용·권한·등급·삭제마다 오르는 번호 (ADR-19)
    updated_at: datetime
    body: str  # Markdown 본문
    space_principals: tuple[str, ...]  # 스페이스 보기 권한 (ADR-21)
    restricted_principals: tuple[str, ...]  # 문서 제한. 제한이 없으면 ("all",)
    classification: Classification


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    chunk_index: int  # 문서 안에서의 순서
    section_path: tuple[str, ...]  # 문서 제목 아래의 제목 경로
    text: str  # 청크 본문 (맥락 헤더 제외)
    section_hash: str  # 섹션 제목 경로와 본문의 해시 (ADR-10)
    part: int  # 섹션 안에서의 순서

    @property
    def chunk_id(self) -> str:
        # 순번 대신 섹션 해시로 만든다. 앞 섹션이 바뀌어도 뒤 섹션의 id가 그대로여야
        # 바뀐 섹션의 청크만 다시 임베딩하고 사라진 청크만 지울 수 있다 (ADR-10)
        return f"{self.doc_id}#{self.section_hash}-{self.part}"
