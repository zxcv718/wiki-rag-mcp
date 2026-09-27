"""검색 필터를 만든다.

권한 조건은 반드시 knn 절 안의 filter에 넣어야 검색 도중에 적용된다(pre-filter, ADR-07).
knn 쿼리 바깥을 bool로 감싸 filter 절에 두거나 post_filter에 두면 사후 필터가 된다(4장).
"""

import re
from collections.abc import Iterable
from datetime import datetime

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


def permission_filter(principals: Iterable[str]) -> list[dict]:
    """스페이스 권한과 문서 제한을 둘 다 만족하는 조건 (ADR-21).

    두 필드 각각에 "사용자 principal 중 하나라도 포함"을 걸고 AND로 묶는다.
    필드가 비어 있는 문서는 어느 terms 조건에도 맞지 않으므로 아무에게도 보이지 않는다(4장 권한 모델).
    """
    allowed = principal_set(principals)
    return [
        {"terms": {"space_principals": allowed}},
        {"terms": {"restricted_principals": allowed}},
    ]


def allows(principals: Iterable[str], space_principals: Iterable[str], restricted_principals: Iterable[str]) -> bool:
    """permission_filter와 같은 규칙을 검색 엔진 밖에서 판단한다. 본문을 줄 때 위키가 다시 확인하는 데 쓴다.

    두 규칙이 어긋나면 검색에는 나오는데 본문은 못 보는(또는 그 반대) 문서가 생기므로, 같은 테스트로 함께 검증한다.
    """
    allowed = set(principal_set(principals))
    return bool(allowed & set(space_principals)) and bool(allowed & set(restricted_principals))


def search_filter(principals: Iterable[str], *, space: str | None = None,
                  updated_after: datetime | None = None) -> dict:
    """권한 조건에 도구 입력의 선택 조건(스페이스, 수정일)을 더한 필터."""
    clauses = permission_filter(principals)
    if space:
        clauses.append({"term": {"space": space}})
    if updated_after:
        clauses.append({"range": {"updated_at": {"gte": updated_after.isoformat()}}})
    return {"bool": {"filter": clauses}}
