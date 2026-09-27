"""권한 판단 규칙 (ADR-07, ADR-21).

검색 저장소(pg_store)는 같은 규칙을 SQL WHERE 절로 검색 안에서 걸고, 위키는 본문을 줄 때 allows로 다시
확인한다. 두 곳이 같은 principal 정규화(principal_set)를 쓰게 해 규칙이 어긋나지 않게 한다.
"""

import re
from collections.abc import Iterable

ALL = "all"
_PRINCIPAL = re.compile(r"^(user|group):[A-Za-z0-9._-]+$")


def validate_stored_principals(values: Iterable[str]) -> tuple[str, ...]:
    """문서에 저장할 권한 목록을 검사한다. all은 허용하고, 빈 목록은 아무도 못 보는 문서로 그대로 둔다."""
    result = tuple(values)
    for p in result:
        if p != ALL and not _PRINCIPAL.match(p):
            raise ValueError(f"잘못된 principal 형식: {p!r}")
    return result


def principal_set(principals: Iterable[str]) -> list[str]:
    """사용자의 principal 목록에 전체 공개(all)를 더해 정렬한다. 형식이 틀리면 거부한다."""
    result = {ALL}
    for p in principals:
        if p == ALL:
            continue
        if not _PRINCIPAL.match(p):
            raise ValueError(f"잘못된 principal 형식: {p!r}")
        result.add(p)
    return sorted(result)


def allows(principals: Iterable[str], space_principals: Iterable[str], restricted_principals: Iterable[str]) -> bool:
    """스페이스 권한과 문서 제한을 둘 다 만족하는가 (ADR-21). 빈 필드는 누구도 만족하지 못한다.

    검색 저장소의 WHERE 조건과 같은 규칙이다. 두 규칙이 어긋나면 검색에는 나오는데 본문은 못 보는(또는 그 반대)
    문서가 생기므로, 권한 테스트셋과 list_recent_changes 일관성 테스트로 함께 검증한다.
    """
    allowed = set(principal_set(principals))
    return bool(allowed & set(space_principals)) and bool(allowed & set(restricted_principals))
