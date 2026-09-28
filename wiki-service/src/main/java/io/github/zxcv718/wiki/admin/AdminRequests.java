package io.github.zxcv718.wiki.admin;

import com.fasterxml.jackson.annotation.JsonProperty;
import io.github.zxcv718.wiki.web.ClassificationName;
import io.github.zxcv718.wiki.web.Principal;
import io.github.zxcv718.wiki.web.WikiId;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 관리자 API의 요청 본문 (README "관리자 API").
 *
 * 권한 목록은 null과 빈 목록을 구분한다. 빼면(null) 기본값 ["all"]이고, 빈 목록은 아무도 볼 수 없다는 뜻으로 그대로
 * 저장한다. 숫자는 박싱 타입으로 받아 빠진 값이 0으로 채워지지 않고 검증에 걸리게 한다.
 */
final class AdminRequests {

    private AdminRequests() {
    }

    record CreateDocument(
            @NotNull @WikiId String docId,
            @NotNull @WikiId String space,
            @NotBlank String title,
            @NotNull String body,
            List<@Principal String> restrictedPrincipals,
            @ClassificationName String classification) {
    }

    record EditContent(@NotBlank String title, @NotNull String body, @NotNull @Positive Integer baseVersion) {
    }

    record Principals(@NotNull List<@Principal String> principals) {
    }

    /**
     * null이면 문서 등급을 지우고 스페이스 기본값을 따른다. 키 자체는 빠지면 안 된다. 빈 본문이나 키 이름 오타가
     * null로 읽히면 기밀 등급이 조용히 풀린다.
     */
    record DocumentClassification(@JsonProperty(required = true) @ClassificationName String classification) {
    }

    record SpaceClassification(@NotNull @ClassificationName String classification) {
    }

    record UserName(@NotBlank String name) {
    }

    /** 12자 이상. bcrypt는 72바이트까지만 쓰므로 그보다 긴 값은 서비스에서 거절한다. */
    record Password(@NotNull @Size(min = 12, message = "12자 이상이어야 합니다.") String password) {
    }

    record Import(
            @NotNull List<@NotNull @Valid SpaceImport> spaces,
            @NotNull List<@NotNull @Valid GroupImport> groups,
            @NotNull List<@NotNull @Valid UserImport> users,
            @NotNull List<@NotNull @Valid DocumentImport> documents) {
    }

    /** 스페이스 권한은 빼지 못하게 한다. 권한이 빠진 스페이스가 기본값으로 조용히 열리면 안 된다. */
    record SpaceImport(
            @NotNull @WikiId String space,
            @NotBlank String title,
            @NotNull List<@Principal String> principals,
            @NotNull @ClassificationName String classification) {
    }

    record GroupImport(@NotNull @WikiId String group, @NotBlank String name) {
    }

    /** groups는 principal이 아니라 그룹 id다 (employees, eng). */
    record UserImport(
            @NotNull @WikiId String userId,
            @NotBlank String name,
            @NotNull List<@NotNull @WikiId String> groups) {
    }

    /** updated_at은 어떤 시간대로 와도 받고, 저장과 응답은 UTC로 한다. */
    record DocumentImport(
            @NotNull @WikiId String docId,
            @NotNull @WikiId String space,
            @NotBlank String title,
            @NotNull String body,
            @NotNull @Positive Integer version,
            @NotNull @Positive Long revision,
            @NotNull OffsetDateTime updatedAt,
            List<@Principal String> restrictedPrincipals,
            @ClassificationName String classification) {
    }
}
