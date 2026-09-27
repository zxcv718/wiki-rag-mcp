"""문서와 청크의 공통 데이터 구조."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


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
