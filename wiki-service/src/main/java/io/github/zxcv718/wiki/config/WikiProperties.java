package io.github.zxcv718.wiki.config;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/**
 * 위키 서비스 설정.
 *
 * 토큰이 비어 있으면 검증에 실패해 서비스가 뜨지 않는다. 인증 정보 없이 열린 상태로 뜨는 것보다 실패하는 편이
 * 안전하기 때문이다(fail closed).
 */
@Validated
@ConfigurationProperties(prefix = "wiki")
public record WikiProperties(
        @NotBlank(message = "WIKI_SERVICE_TOKEN이 비어 있습니다") String serviceToken,
        @NotBlank(message = "WIKI_ADMIN_TOKEN이 비어 있습니다") String adminToken,
        @NotBlank String publicUrl,
        // 인덱서와 같은 값이어야 같은 문서의 이벤트가 같은 스트림으로 간다 (ADR-19)
        @Min(1) int eventPartitions) {

    /**
     * 두 토큰이 같으면 검색 서버가 가진 서비스 토큰으로 문서를 고칠 수 있게 된다. 설정 실수로 권한 분리가 사라지지 않게
     * 뜨지 않는다.
     */
    @AssertTrue(message = "WIKI_SERVICE_TOKEN과 WIKI_ADMIN_TOKEN이 같습니다. 서로 다른 값을 넣어 주세요")
    public boolean isTokensDistinct() {
        return serviceToken == null || !serviceToken.equals(adminToken);
    }

    /** 문서 링크. 설정 끝에 슬래시가 있어도 같은 링크가 나오게 한다. */
    public String documentUrl(String docId) {
        String base = publicUrl.endsWith("/") ? publicUrl.substring(0, publicUrl.length() - 1) : publicUrl;
        return base + "/doc/" + docId;
    }
}
