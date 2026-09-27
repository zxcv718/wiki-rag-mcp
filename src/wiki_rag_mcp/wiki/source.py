"""위키 접근 경계. 권한과 문서의 원천은 위키다 (ADR-01).

파일로 된 가상 위키(FileWikiSource)와 Spring 위키 API(HttpWikiSource) 두 구현이 있다.
검색 서버와 인덱서의 나머지 코드는 이 인터페이스만 안다.

- DocumentSource: 전체 색인에 필요한 연산
- IndexSource: 이벤트로 증분 색인할 때 더 필요한 연산 (ADR-19의 상태 재조회, 야간 정합성 배치)
- WikiSource: 검색 서버가 요청마다 쓰는 연산 (그룹 해석, 본문 재확인)
"""

from dataclasses import dataclass
from typing import Protocol

from wiki_rag_mcp.models import Document


class WikiUnavailableError(RuntimeError):
    """위키에서 문서나 권한 정보를 읽지 못했다. 일부만 반영한 결과를 주지 않고 요청 전체를 실패시킨다."""


class GroupLookupError(WikiUnavailableError):
    """그룹 멤버십을 읽지 못했다. 사용자 id만으로 검색하지 않고 요청 전체를 실패시킨다 (ADR-08)."""


@dataclass(frozen=True)
class DocState:
    """위키가 지금 알고 있는 문서 하나의 상태.

    삭제된 문서는 document가 None이고 revision이 남는다. 위키에 한 번도 없던 문서는 revision도 None이다.
    """

    doc_id: str
    revision: int | None
    document: Document | None

    @property
    def deleted(self) -> bool:
        return self.document is None


@dataclass(frozen=True)
class RevisionEntry:
    doc_id: str
    revision: int
    deleted: bool


class DocumentSource(Protocol):
    def documents(self) -> list[Document]:
        """색인할 전체 문서 (삭제된 문서 제외)."""
        ...

    def space_title(self, space: str) -> str:
        """맥락 헤더에 쓰는 스페이스 표시 이름."""
        ...


class IndexSource(DocumentSource, Protocol):
    def state(self, doc_id: str) -> DocState:
        """캐시를 거치지 않은 현재 상태. 이벤트는 신호로만 쓰고 상태는 여기서 다시 읽는다 (ADR-19).

        위키를 읽지 못하면 WikiUnavailableError.
        """
        ...

    def revisions(self) -> list[RevisionEntry]:
        """삭제된 문서까지 포함한 모든 문서의 revision. 야간 정합성 배치가 인덱스와 비교한다 (5장 "장애 대응")."""
        ...


class WikiSource(Protocol):
    def documents(self) -> list[Document]:
        """색인할 전체 문서."""
        ...

    def document(self, doc_id: str) -> Document | None:
        """권한을 따지지 않는 조회 (색인·관리용). 없는 문서면 None."""
        ...

    def document_for(self, doc_id: str, principals: list[str]) -> Document | None:
        """사용자에게 줄 문서. 권한은 위키가 두 층 모두 따져 판단한다 (ADR-07 본문 재확인, ADR-21).

        없는 문서와 볼 수 없는 문서를 구분하지 않고 둘 다 None이다. 위키를 읽지 못하면 WikiUnavailableError.
        """
        ...

    def groups_of(self, user_id: str) -> list[str]:
        """사용자의 그룹 principal 목록. 읽지 못하면 GroupLookupError."""
        ...

    def space_title(self, space: str) -> str:
        """맥락 헤더에 쓰는 스페이스 표시 이름."""
        ...
