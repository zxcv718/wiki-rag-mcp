package io.github.zxcv718.wiki.domain;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;

/** version과 revision 규칙 (ADR-19). HTTP를 거치는 같은 경우는 AdminApiTest에 있다. */
class DocumentTest {

    private static final Instant CREATED = Instant.parse("2026-09-01T00:00:00Z");
    private static final Instant LATER = Instant.parse("2026-09-02T00:00:00Z");

    private final Space infra = new Space("infra", "플랫폼·SRE", List.of("group:infra"), Classification.GENERAL);

    private Document newDocument() {
        return Document.create("infra-100", infra, "제목", "본문", null, null, CREATED);
    }

    @Test
    void newDocumentStartsAtOneAndIsUnrestricted() {
        Document document = newDocument();
        assertThat(document.getVersion()).isEqualTo(1);
        assertThat(document.getRevision()).isEqualTo(1);
        assertThat(document.getRestrictions()).containsExactly("all");
    }

    @Test
    void contentEditBumpsVersionRevisionAndUpdatedAt() {
        Document document = newDocument();
        document.editContent("새 제목", "새 본문", 1, LATER);
        assertThat(document.getVersion()).isEqualTo(2);
        assertThat(document.getRevision()).isEqualTo(2);
        assertThat(document.getUpdatedAt()).isEqualTo(LATER);
    }

    @Test
    void staleBaseVersionIsRejectedWithoutChange() {
        Document document = newDocument();
        assertThatThrownBy(() -> document.editContent("새 제목", "새 본문", 0, LATER))
                .isInstanceOf(VersionConflictException.class);
        assertThat(document.getRevision()).isEqualTo(1);
        assertThat(document.getTitle()).isEqualTo("제목");
    }

    @Test
    void permissionClassificationAndDeleteBumpOnlyRevision() {
        Document document = newDocument();
        document.changeRestrictions(List.of("group:dba"));
        document.changeClassification(Classification.CONFIDENTIAL);
        document.spaceAccessChanged();
        document.delete();
        assertThat(document.getVersion()).isEqualTo(1);
        assertThat(document.getRevision()).isEqualTo(5);
        assertThat(document.getUpdatedAt()).isEqualTo(CREATED);
        assertThat(document.isDeleted()).isTrue();
    }

    @Test
    void classificationIsHigherOfSpaceAndDocument() {
        assertThat(Classification.effective(Classification.GENERAL, null)).isEqualTo(Classification.GENERAL);
        assertThat(Classification.effective(Classification.GENERAL, Classification.CONFIDENTIAL))
                .isEqualTo(Classification.CONFIDENTIAL);
        assertThat(Classification.effective(Classification.CONFIDENTIAL, Classification.GENERAL))
                .isEqualTo(Classification.CONFIDENTIAL);
    }
}
