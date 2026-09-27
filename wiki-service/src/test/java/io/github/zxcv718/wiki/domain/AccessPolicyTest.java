package io.github.zxcv718.wiki.domain;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

/** 권한 규칙 (ADR-21). Python 쪽 tests의 권한 테스트와 같은 경우를 둔다. */
class AccessPolicyTest {

    private static final List<String> INFRA_SPACE = List.of("group:infra", "group:eng");
    private static final List<String> DBA_ONLY = List.of("group:dba");

    private static Set<String> principals(String userId, String... groups) {
        return AccessPolicy.principalsOf(new User(userId, userId, List.of(groups)));
    }

    @Test
    void principalSetHasUserGroupsAndAll() {
        assertThat(principals("jiho", "employees", "eng"))
                .containsExactlyInAnyOrder("all", "user:jiho", "group:employees", "group:eng");
    }

    @Test
    void spaceMemberOutsideRestrictionCannotSee() {
        assertThat(AccessPolicy.canView(principals("seoyeon", "employees", "eng", "infra"), INFRA_SPACE, DBA_ONLY))
                .isFalse();
    }

    @Test
    void restrictionTargetOutsideSpaceCannotSee() {
        assertThat(AccessPolicy.canView(principals("minjun", "employees", "data", "dba"), INFRA_SPACE, DBA_ONLY))
                .isFalse();
    }

    @Test
    void memberOfBothCanSee() {
        assertThat(AccessPolicy.canView(principals("taeyang", "employees", "eng", "infra", "dba"),
                INFRA_SPACE, DBA_ONLY)).isTrue();
    }

    @Test
    void userPrincipalCountsAsRestrictionTarget() {
        assertThat(AccessPolicy.canView(principals("taeyang", "infra"), INFRA_SPACE, List.of("user:taeyang")))
                .isTrue();
        assertThat(AccessPolicy.canView(principals("seoyeon", "infra"), INFRA_SPACE, List.of("user:taeyang")))
                .isFalse();
    }

    @Test
    void emptyListsAreVisibleToNobody() {
        Set<String> everyone = principals("taeyang", "employees", "eng", "infra", "dba");
        assertThat(AccessPolicy.canView(everyone, List.of(), List.of("all"))).isFalse();
        assertThat(AccessPolicy.canView(everyone, INFRA_SPACE, List.of())).isFalse();
    }

    @Test
    void allMeansEveryKnownUser() {
        assertThat(AccessPolicy.canView(principals("junpark"), List.of("all"), List.of("all"))).isTrue();
    }
}
