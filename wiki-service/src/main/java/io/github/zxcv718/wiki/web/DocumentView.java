package io.github.zxcv718.wiki.web;

import io.github.zxcv718.wiki.config.WikiProperties;
import io.github.zxcv718.wiki.domain.Document;

/** 문서 한 건의 응답. 삭제된 문서는 본문과 권한 없이 revision만 담은 tombstone으로 답한다. */
public sealed interface DocumentView permits DocumentState, DocumentTombstone {

    static DocumentView of(Document document, WikiProperties properties) {
        if (document.isDeleted()) {
            return DocumentTombstone.of(document);
        }
        return DocumentState.of(document, properties);
    }
}
