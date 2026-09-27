package io.github.zxcv718.wiki.internal;

import io.github.zxcv718.wiki.domain.DocumentRevision;
import io.github.zxcv718.wiki.web.DocumentState;
import java.util.List;

/** 내부 API의 응답 형태 (README "내부 API"). */
final class InternalResponses {

    private InternalResponses() {
    }

    record SpaceSummary(String space, String title) {
    }

    record SpaceList(List<SpaceSummary> spaces) {
    }

    /** next는 다음 요청의 after 값이다. 마지막 페이지면 null이다. */
    record DocumentPage(List<DocumentState> documents, String next) {
    }

    record RevisionList(List<DocumentRevision> documents) {
    }

    record UserGroups(String userId, List<String> groups) {
    }
}
