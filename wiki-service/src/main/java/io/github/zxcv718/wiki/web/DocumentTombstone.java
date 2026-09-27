package io.github.zxcv718.wiki.web;

import io.github.zxcv718.wiki.domain.Document;

/**
 * 삭제된 문서. 404로 답하지 않고 revision을 알려 주는 이유는, 인덱서가 삭제보다 늦게 도착한 옛 이벤트를 revision으로
 * 알아보고 버리게 하기 위해서다 (ADR-19).
 */
public record DocumentTombstone(String docId, long revision, boolean deleted) implements DocumentView {

    public static DocumentTombstone of(Document document) {
        return new DocumentTombstone(document.getId(), document.getRevision(), true);
    }
}
