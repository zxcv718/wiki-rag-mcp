"""위키 서비스의 revision 규칙(wiki-service/README.md)을 따르는 메모리 위키. 인덱서 테스트가 쓴다.

바꾸는 연산마다 위키 서비스와 같이 revision을 올리고, 아웃박스가 낼 이벤트를 돌려준다. 테스트는 이 이벤트를
원하는 순서로 섞거나 빼서 인덱서에 넣는다.
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from wiki_rag_mcp.indexing.events import Event
from wiki_rag_mcp.models import Classification, Document
from wiki_rag_mcp.search.filters import ALL
from wiki_rag_mcp.wiki.source import DocState, RevisionEntry, WikiUnavailableError

LEVELS = list(Classification)


@dataclass
class Space:
    title: str
    viewers: tuple[str, ...]
    classification: Classification = Classification.GENERAL


@dataclass
class Page:
    space: str
    title: str
    body: str
    restricted: tuple[str, ...] = (ALL,)
    classification: Classification | None = None
    version: int = 1
    revision: int = 1
    deleted: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime(2026, 9, 1, tzinfo=UTC))


class FakeWiki:
    def __init__(self, spaces: dict[str, Space]):
        self.spaces = spaces
        self.pages: dict[str, Page] = {}
        self.unavailable = False  # True면 상태 조회가 위키 장애처럼 실패한다
        self.reads = 0

    def _event(self, doc_id: str, kind: str) -> Event:
        return Event(doc_id, self.pages[doc_id].revision, kind)

    def _live(self, doc_id: str) -> Page:
        page = self.pages[doc_id]
        assert not page.deleted, f"{doc_id}는 삭제됐다"
        return page

    # 위키를 바꾸는 연산. 모두 revision을 1 올린다

    def create(self, doc_id: str, space: str, title: str, body: str, restricted: tuple[str, ...] = (ALL,)) -> Event:
        self.pages[doc_id] = Page(space, title, body, restricted)
        return self._event(doc_id, "CONTENT_CHANGED")

    def edit(self, doc_id: str, *, title: str | None = None, body: str | None = None) -> Event:
        page = self._live(doc_id)
        page.title, page.body = title or page.title, body if body is not None else page.body
        page.version += 1
        page.revision += 1
        page.updated_at += timedelta(days=1)
        return self._event(doc_id, "CONTENT_CHANGED")

    def restrict(self, doc_id: str, principals: tuple[str, ...]) -> Event:
        page = self._live(doc_id)
        page.restricted = principals
        page.revision += 1
        return self._event(doc_id, "ACL_CHANGED")

    def classify(self, doc_id: str, level: Classification | None) -> Event:
        page = self._live(doc_id)
        page.classification = level
        page.revision += 1
        return self._event(doc_id, "ACL_CHANGED")

    def delete(self, doc_id: str) -> Event:
        page = self._live(doc_id)
        page.deleted = True
        page.revision += 1
        return self._event(doc_id, "DELETED")

    def set_viewers(self, space: str, principals: tuple[str, ...]) -> list[Event]:
        self.spaces[space] = replace(self.spaces[space], viewers=principals)
        return self._bump_space(space)

    def set_space_classification(self, space: str, level: Classification) -> list[Event]:
        self.spaces[space] = replace(self.spaces[space], classification=level)
        return self._bump_space(space)

    def _bump_space(self, space: str) -> list[Event]:
        events = []
        for doc_id, page in sorted(self.pages.items()):
            if page.space == space and not page.deleted:
                page.revision += 1
                events.append(self._event(doc_id, "ACL_CHANGED"))
        return events

    # IndexSource

    def document(self, doc_id: str) -> Document | None:
        page = self.pages.get(doc_id)
        if page is None or page.deleted:
            return None
        space = self.spaces[page.space]
        level = max(space.classification, page.classification or space.classification, key=LEVELS.index)
        return Document(doc_id=doc_id, title=page.title, space=page.space, url=f"https://wiki.test/doc/{doc_id}",
                        version=page.version, revision=page.revision, updated_at=page.updated_at, body=page.body,
                        space_principals=space.viewers, restricted_principals=page.restricted, classification=level)

    def state(self, doc_id: str) -> DocState:
        self.reads += 1
        if self.unavailable:
            raise WikiUnavailableError("위키 장애 (테스트)")
        page = self.pages.get(doc_id)
        return DocState(doc_id, page.revision if page else None, self.document(doc_id))

    def documents(self) -> list[Document]:
        return [d for d in (self.document(i) for i in sorted(self.pages)) if d is not None]

    def revisions(self) -> list[RevisionEntry]:
        return [RevisionEntry(i, p.revision, p.deleted) for i, p in sorted(self.pages.items())]

    def space_title(self, space: str) -> str:
        return self.spaces[space].title
