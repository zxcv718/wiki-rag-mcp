"""요청한 사용자의 principal 목록을 만든다. 그룹 멤버십은 인덱스에 넣지 않고 여기서 해석한다 (ADR-08)."""

from wiki_rag_mcp.models import valid_user_id
from wiki_rag_mcp.wiki.source import GroupSource


def user_id_of(user: str) -> str:
    """`user:alice`나 `alice`에서 위키 사용자 id를 꺼낸다. id는 위키 API 경로에 들어가므로 형식부터 확인한다."""
    user_id = user.removeprefix("user:")
    if not valid_user_id(user_id):
        raise ValueError(f"잘못된 사용자 id: {user!r}")
    return user_id


def principals_for(user: str, groups: GroupSource) -> list[str]:
    """사용자 id와 그룹으로 principal 목록을 만든다.

    그룹을 읽지 못하면 GroupLookupError가 그대로 올라간다. 사용자 id만으로 검색하면 누출은 아니지만
    사용자가 문서가 없다고 잘못 믿게 되므로, 결과를 줄이지 않고 요청 전체를 실패시킨다. 위키에 없는 사용자
    (UnknownUserError)도 같은 이유로 그룹 없는 사용자로 보지 않는다.
    """
    user_id = user_id_of(user)
    return [f"user:{user_id}", *groups.groups_of(user_id)]
