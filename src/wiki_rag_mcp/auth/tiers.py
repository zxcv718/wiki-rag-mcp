"""문서 등급과 클라이언트 신뢰 등급으로 반환 범위를 정한다 (ADR-17).

| 문서 등급 | 외부 LLM 클라이언트 | 사내 LLM 클라이언트 |
|---|---|---|
| 일반 | 스니펫·본문 | 스니펫·본문 |
| 기밀 | 제목·링크만 | 스니펫·본문 |

클라이언트 등급은 요청 파라미터로 받지 않는다.
M1(stdio)은 실행 환경 설정으로, M5부터는 OAuth 클라이언트 id로 서버가 정한다.
"""

from enum import StrEnum

from wiki_rag_mcp.models import Classification


class ClientTier(StrEnum):
    EXTERNAL = "external"
    INTERNAL = "internal"


def content_allowed(classification: Classification | str, tier: ClientTier | str) -> bool:
    """스니펫과 본문을 줘도 되는가. 모르는 등급은 기밀로 본다."""
    try:
        level = Classification(classification)
    except ValueError:
        level = Classification.CONFIDENTIAL
    if level == Classification.GENERAL:
        return True
    return ClientTier(tier) == ClientTier.INTERNAL
