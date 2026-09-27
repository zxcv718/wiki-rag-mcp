"""요청한 사용자의 principal 목록을 만든다. 그룹 멤버십은 인덱스에 넣지 않고 여기서 해석한다 (ADR-08)."""

import re

from wiki_rag_mcp.wiki.source import WikiSource

_USER_ID = re.compile(r"[A-Za-z0-9._-]+")


def principals_for(user: str, source: WikiSource) -> list[str]:
    """사용자 id와 그룹으로 principal 목록을 만든다.

    그룹을 읽지 못하면 GroupLookupError가 그대로 올라간다. 사용자 id만으로 검색하면 누출은 아니지만
    사용자가 문서가 없다고 잘못 믿게 되므로, 결과를 줄이지 않고 요청 전체를 실패시킨다.
    """
    user_id = user.removeprefix("user:")
    if not _USER_ID.fullmatch(user_id):
        raise ValueError(f"잘못된 사용자 id: {user!r}")
    return [f"user:{user_id}", *source.groups_of(user_id)]
