package io.github.zxcv718.wiki.domain;

import java.util.Collection;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * 문서를 볼 수 있는지 정하는 규칙 (ADR-21). Python 쪽 search/filters.py의 principal_set, allows와 같은 규칙이다.
 *
 * 검색 서버는 같은 규칙을 검색 쿼리 안에서 걸고(pre-filter, ADR-07), 위키는 본문을 줄 때 이 규칙으로 다시 확인한다.
 * 두 곳의 규칙이 어긋나면 검색에는 나오는데 본문은 못 보는(또는 그 반대) 문서가 생긴다.
 */
public final class AccessPolicy {

    /** 전체 공개. 공개 문서도 빈 목록이 아니라 반드시 all을 적는다. */
    public static final String ALL = "all";

    private AccessPolicy() {
    }

    /** 사용자의 principal 집합: user:{id}, 소속 그룹의 group:{id}, all. */
    public static Set<String> principalsOf(User user) {
        Set<String> principals = new HashSet<>();
        principals.add(ALL);
        principals.add("user:" + user.getId());
        for (String group : user.getGroupIds()) {
            principals.add("group:" + group);
        }
        return principals;
    }

    /**
     * 스페이스 보기 권한과 문서 제한을 둘 다 만족해야 볼 수 있다. Confluence와 같은 의미로, 스페이스 멤버라도
     * 제한 대상이 아니면 못 보고, 제한 대상이라도 스페이스 멤버가 아니면 못 본다.
     * 빈 목록은 어떤 집합과도 겹치지 않으므로 아무도 볼 수 없다. 권한 정보가 빠진 문서가 전체 공개로 새지 않게 한다.
     */
    public static boolean canView(Set<String> principals, Collection<String> spaceViewers,
                                  Collection<String> restrictions) {
        return !Collections.disjoint(principals, spaceViewers) && !Collections.disjoint(principals, restrictions);
    }
}
