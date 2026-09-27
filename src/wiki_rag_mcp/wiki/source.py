"""위키 접근 경계. 권한과 문서의 원천은 위키다 (ADR-01).

M1은 파일로 된 가상 위키(FileWikiSource)를 쓰고, M3에서 Spring 위키 API 구현으로 바꿔 끼운다.
검색 서버의 나머지 코드는 이 인터페이스만 안다.
"""

from typing import Protocol

from wiki_rag_mcp.models import Document


class WikiUnavailableError(RuntimeError):
    """위키에서 문서나 권한 정보를 읽지 못했다. 일부만 반영한 결과를 주지 않고 요청 전체를 실패시킨다."""


class GroupLookupError(WikiUnavailableError):
    """그룹 멤버십을 읽지 못했다. 사용자 id만으로 검색하지 않고 요청 전체를 실패시킨다 (ADR-08)."""


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
