package io.github.zxcv718.wiki.web;

import io.github.zxcv718.wiki.config.WikiProperties;
import io.github.zxcv718.wiki.domain.Document;
import java.time.Instant;
import java.util.Collection;
import java.util.List;

/**
 * 문서 상태 (README "내부 API"). 인덱서가 이벤트를 받을 때마다 다시 읽는 형태다 (ADR-19).
 *
 * space_principals는 읽는 시점의 스페이스 보기 권한이고, classification은 스페이스 기본값을 반영한 최종 등급이다.
 * 권한 목록은 정렬해서 내보낸다. 같은 상태면 같은 응답이 나와야 인덱서가 비교하기 쉽다.
 */
public record DocumentState(
        String docId,
        String title,
        String space,
        String url,
        int version,
        long revision,
        Instant updatedAt,
        String body,
        List<String> spacePrincipals,
        List<String> restrictedPrincipals,
        String classification,
        boolean deleted) implements DocumentView {

    public static DocumentState of(Document document, WikiProperties properties) {
        return new DocumentState(
                document.getId(),
                document.getTitle(),
                document.getSpace().getId(),
                properties.documentUrl(document.getId()),
                document.getVersion(),
                document.getRevision(),
                document.getUpdatedAt(),
                document.getBody(),
                sorted(document.getSpace().getViewers()),
                sorted(document.getRestrictions()),
                document.effectiveClassification().value(),
                false);
    }

    static List<String> sorted(Collection<String> values) {
        return values.stream().sorted().toList();
    }
}
