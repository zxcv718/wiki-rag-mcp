package io.github.zxcv718.wiki.internal;

import io.github.zxcv718.wiki.internal.InternalResponses.DocumentPage;
import io.github.zxcv718.wiki.internal.InternalResponses.RevisionList;
import io.github.zxcv718.wiki.internal.InternalResponses.SpaceList;
import io.github.zxcv718.wiki.internal.InternalResponses.UserGroups;
import io.github.zxcv718.wiki.web.DocumentState;
import io.github.zxcv718.wiki.web.DocumentView;
import io.github.zxcv718.wiki.web.WikiId;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 검색 서버와 인덱서가 서비스 토큰으로 부르는 읽기 전용 API (README "내부 API"). */
@RestController
@RequestMapping("/internal")
class InternalController {

    private final WikiReadService reads;

    InternalController(WikiReadService reads) {
        this.reads = reads;
    }

    @GetMapping("/spaces")
    SpaceList spaces() {
        return reads.spaces();
    }

    @GetMapping("/documents")
    DocumentPage documents(
            @RequestParam(required = false) @WikiId String after,
            @RequestParam(defaultValue = "100") @Min(1) @Max(500) int limit) {
        return reads.documents(after, limit);
    }

    @GetMapping("/documents/{doc_id}")
    DocumentView document(@PathVariable("doc_id") @WikiId String docId) {
        return reads.document(docId);
    }

    @GetMapping("/revisions")
    RevisionList revisions() {
        return reads.revisions();
    }

    @GetMapping("/users/{user_id}/groups")
    UserGroups groups(@PathVariable("user_id") @WikiId String userId) {
        return reads.groupsOf(userId);
    }

    @GetMapping("/users/{user_id}/documents/{doc_id}")
    DocumentState documentFor(@PathVariable("user_id") @WikiId String userId,
                              @PathVariable("doc_id") @WikiId String docId) {
        return reads.documentFor(userId, docId);
    }
}
