package io.github.zxcv718.wiki.domain;

public class VersionConflictException extends RuntimeException {

    public VersionConflictException(String docId, int currentVersion, int baseVersion) {
        super("문서 %s의 현재 version은 %d인데 base_version이 %d입니다. 문서를 다시 읽은 뒤 고쳐 주세요."
                .formatted(docId, currentVersion, baseVersion));
    }
}
