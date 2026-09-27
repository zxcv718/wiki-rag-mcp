"""MCP 서버. 답변을 만들지 않고 권한에 맞는 근거만 돌려준다 (ADR-04, ADR-05).

M1은 stdio로만 돈다. 사용자는 실행 환경의 WIKI_USER로, 클라이언트 신뢰 등급은 WIKI_CLIENT_TIER로 정한다(ADR-06).
표준 출력은 MCP 메시지 전용이라 로그를 찍지 않는다.
"""

from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from wiki_rag_mcp.auth.principals import principals_for
from wiki_rag_mcp.auth.tiers import ClientTier
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.search.store import OpenSearchStore
from wiki_rag_mcp.server.responses import search_response
from wiki_rag_mcp.wiki.source import GroupLookupError, WikiSource

MAX_TOP_K = 10
MAX_QUERY_CHARS = 500

# 명세상 클라이언트는 annotation을 신뢰하지 않으므로, 실제 보장은 쓰기 경로가 없는 설계다 (ADR-16)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

SEARCH_DESCRIPTION = """사내 위키에서 질문과 관련된 근거 조각을 찾는다. 사용자가 볼 권한이 있는 문서만 나온다.

쓸 때: 사내 규정, 절차, 장애 대응, 약어처럼 사내 위키에 있을 내용을 묻거나 관련 문서를 찾을 때.
쓰지 말 때: 일반 상식이나 공개된 기술 질문, 특정 기간에 바뀐 문서 목록이 필요할 때.

결과의 snippet은 근거로만 쓰고, 답할 때 문서 제목·섹션·버전을 출처로 밝힌다. 결과가 없거나 관련이 약하면
위키에 근거가 없다고 답한다. notes에 "오래된 문서"가 있으면 최신 정보가 아닐 수 있음을 알린다."""


class Services:
    """도구가 쓰는 의존성. 테스트에서 바꿔 끼울 수 있게 한곳에 모은다."""

    def __init__(self, settings: Settings, source: WikiSource, store: OpenSearchStore, embedder: Any):
        self.settings = settings
        self.source = source
        self.store = store
        self.embedder = embedder

    def principals(self) -> list[str]:
        if not self.settings.user:
            raise ToolError("사용자가 설정되지 않았습니다. MCP 서버 실행 환경에 WIKI_USER를 지정하세요.")
        try:
            return principals_for(self.settings.user, self.source)
        except GroupLookupError as e:
            # 권한을 덜 반영한 결과를 주지 않고 요청 전체를 실패시킨다 (ADR-08)
            raise ToolError("권한 정보를 확인할 수 없어 검색하지 않았습니다. 잠시 뒤 다시 시도하세요.") from e

    @property
    def tier(self) -> ClientTier:
        return ClientTier(self.settings.client_tier)


def _parse_date(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise ToolError(f"{name}는 ISO 8601 날짜여야 합니다. 예: 2026-09-01") from e
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_server(services: Services) -> MCPServer:
    server = MCPServer(
        name="wiki-rag-mcp",
        instructions="사내 위키 검색 서버입니다. 사용자 권한에 맞는 근거만 돌려주며 답변은 만들지 않습니다.",
    )

    @server.tool(name="search_wiki", title="사내 위키 검색", description=SEARCH_DESCRIPTION, annotations=READ_ONLY)
    def search_wiki(query: str, space: str | None = None, top_k: int = 5,
                    updated_after: str | None = None) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > MAX_QUERY_CHARS:
            raise ToolError(f"query는 1~{MAX_QUERY_CHARS}자여야 합니다.")
        top_k = max(1, min(top_k, MAX_TOP_K))
        principals = services.principals()
        hits = services.store.knn_search(services.embedder.encode_query(query), principals, top_k,
                                         space=space or None, updated_after=_parse_date(updated_after, "updated_after"))
        return search_response(hits, services.tier, datetime.now(UTC))

    return server


def main() -> None:
    from wiki_rag_mcp.indexing.embedder import Embedder
    from wiki_rag_mcp.wiki.files import FileWikiSource

    settings = Settings.from_env()
    services = Services(settings, FileWikiSource(settings.wiki_dir), OpenSearchStore.from_settings(settings),
                        Embedder())
    build_server(services).run("stdio")


if __name__ == "__main__":
    main()
