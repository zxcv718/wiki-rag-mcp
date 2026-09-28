"""Spring 위키 서비스의 내부 API를 읽는다 (wiki-service/README.md).

인덱서가 쓰는 연산(IndexSource)과 검색 서버가 요청마다 쓰는 연산(WikiSource: 그룹 해석, 본문 재확인)을
모두 구현한다. 위키에 붙는 요청은 사용자 토큰이 아니라 서비스 자격 증명으로 보낸다 (ADR-06).

위키의 응답이 계약과 다르면 추측해서 읽지 않고 실패시킨다. 권한 정보를 잘못 읽으면 누출로 이어지기 때문이다.
"""

from datetime import datetime
from typing import Any

import httpx

from wiki_rag_mcp import telemetry
from wiki_rag_mcp.models import Classification, Document, valid_doc_id, valid_user_id
from wiki_rag_mcp.search.filters import valid_groups, validate_stored_principals
from wiki_rag_mcp.wiki.source import (
    DocState,
    GroupLookupError,
    RevisionEntry,
    UnknownUserError,
    WikiUnavailableError,
)

TIMEOUT_SECONDS = 5.0
PAGE_SIZE = 200


def _document(data: dict[str, Any]) -> Document:
    return Document(
        doc_id=data["doc_id"],
        title=data["title"],
        space=data["space"],
        url=data["url"],
        version=int(data["version"]),
        revision=int(data["revision"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
        body=data["body"],
        space_principals=validate_stored_principals(data["space_principals"]),
        restricted_principals=validate_stored_principals(data["restricted_principals"]),
        classification=Classification(data["classification"]),
    )


class HttpWikiSource:
    def __init__(self, base_url: str, service_token: str, *, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        telemetry.instrument_http(self.client)
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {service_token}"}
        self._space_titles: dict[str, str] | None = None

    def _get(self, path: str, params: dict[str, Any] | None = None, *, missing_ok: bool = False) -> Any:
        try:
            response = self.client.get(self.base_url + path, params=params, headers=self.headers)
        except httpx.HTTPError as e:
            raise WikiUnavailableError(f"위키에 연결하지 못했다: {path}") from e
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code != 200:
            # 401은 설정 문제, 5xx는 위키 장애다. 어느 쪽이든 일부만 반영하지 않고 실패시킨다
            raise WikiUnavailableError(f"위키 응답 {response.status_code}: {path}")
        try:
            return response.json()
        except ValueError as e:
            raise WikiUnavailableError(f"위키 응답이 JSON이 아니다: {path}") from e

    def documents(self) -> list[Document]:
        result: list[Document] = []
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": PAGE_SIZE}
            if after:
                params["after"] = after
            page = self._get("/internal/documents", params)
            result.extend(_document(d) for d in page["documents"])
            after = page.get("next")
            if not after:
                return result

    def document(self, doc_id: str) -> Document | None:
        return self.state(doc_id).document

    def groups_of(self, user_id: str) -> list[str]:
        # user_id도 URL 경로에 들어가므로 doc_id처럼 먼저 거른다
        if not valid_user_id(user_id):
            raise ValueError(f"잘못된 사용자 id: {user_id!r}")
        try:
            data = self._get(f"/internal/users/{user_id}/groups", missing_ok=True)
        except WikiUnavailableError as e:
            raise GroupLookupError(str(e)) from e
        if data is None:
            raise UnknownUserError(f"위키에 없는 사용자: {user_id}")
        # 그룹 자리에 user:나 all이 오면 다른 사람의 문서가 열린다. 계약과 다르면 그룹을 못 읽은 것으로 본다
        groups = data.get("groups") if isinstance(data, dict) and data.get("user_id") == user_id else None
        if not valid_groups(groups):
            raise GroupLookupError(f"위키의 그룹 응답이 계약과 다르다: {user_id}")
        return list(groups)

    def document_for(self, doc_id: str, user_id: str) -> Document | None:
        if not valid_doc_id(doc_id) or not valid_user_id(user_id):
            raise ValueError(f"잘못된 id: {doc_id!r}, {user_id!r}")
        data = self._get(f"/internal/users/{user_id}/documents/{doc_id}", missing_ok=True)
        if data is None:
            return None
        # 읽을 수 없는 응답은 doc_id와 관계없이 같은 오류가 되게 한다. 필드 이름이 새어 나가지도 않는다
        try:
            doc = None if data.get("deleted") else _document(data)
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise WikiUnavailableError(f"위키의 문서 응답이 계약과 다르다: {doc_id}") from e
        if doc is not None and doc.doc_id != doc_id:
            raise WikiUnavailableError(f"요청과 다른 문서가 왔다: {doc_id}")
        return doc

    def state(self, doc_id: str) -> DocState:
        # doc_id가 URL 경로에 들어가므로 "../" 같은 값으로 다른 API를 부르지 못하게 먼저 거른다
        if not valid_doc_id(doc_id):
            raise ValueError(f"잘못된 doc_id: {doc_id!r}")
        data = self._get(f"/internal/documents/{doc_id}", missing_ok=True)
        if data is None:
            return DocState(doc_id, revision=None, document=None)
        if data.get("deleted"):
            return DocState(doc_id, revision=int(data["revision"]), document=None)
        doc = _document(data)
        return DocState(doc_id, revision=doc.revision, document=doc)

    def revisions(self) -> list[RevisionEntry]:
        data = self._get("/internal/revisions")
        return [RevisionEntry(d["doc_id"], int(d["revision"]), bool(d["deleted"])) for d in data["documents"]]

    def space_title(self, space: str) -> str:
        # 스페이스 이름을 바꾸는 API는 없어서, 처음 한 번 읽어 둔다
        if self._space_titles is None:
            data = self._get("/internal/spaces")
            self._space_titles = {s["space"]: s["title"] for s in data["spaces"]}
        return self._space_titles.get(space, space)
