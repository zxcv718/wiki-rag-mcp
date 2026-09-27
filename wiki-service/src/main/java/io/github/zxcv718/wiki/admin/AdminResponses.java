package io.github.zxcv718.wiki.admin;

import io.github.zxcv718.wiki.domain.Space;
import io.github.zxcv718.wiki.domain.User;
import java.util.List;

/** 관리자 API의 응답 중 문서 상태가 아닌 것. README가 형태를 정하지 않은 응답이라 요청한 결과를 그대로 보여 준다. */
final class AdminResponses {

    private AdminResponses() {
    }

    record ImportResult(int spaces, int groups, int users, int documents) {
    }

    /** documents_changed는 revision이 오르고 ACL_CHANGED를 낸 문서 수다. */
    record SpaceState(String space, String title, List<String> principals, String classification,
                      int documentsChanged) {

        static SpaceState of(Space space, int documentsChanged) {
            return new SpaceState(space.getId(), space.getTitle(), space.getViewers().stream().sorted().toList(),
                    space.getDefaultClassification().value(), documentsChanged);
        }
    }

    record UserState(String userId, String name, List<String> groups) {

        static UserState of(User user) {
            return new UserState(user.getId(), user.getName(),
                    user.getGroupIds().stream().map(g -> "group:" + g).sorted().toList());
        }
    }
}
