package io.github.zxcv718.wiki.auth;

import jakarta.validation.Valid;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.StringUtils;
import org.springframework.validation.annotation.Validated;

/**
 * 인가 서버 설정 (README "인가 서버"의 설정 표).
 *
 * 발급자가 https면 운영으로 본다. 로컬은 http://127.0.0.1이라 이 기준으로 로컬과 운영이 갈린다. 운영에서 위험한 설정은
 * 뜨지 않게 막는다(fail closed). 서명 키가 없으면 다시 시작할 때마다 키가 바뀌어 발급한 토큰이 모두 무효가 되고,
 * http localhost CIMD를 켜 두면 누구나 client_id로 서버 자신의 로컬 포트에 요청을 보내게 할 수 있다.
 */
@Validated
@ConfigurationProperties(prefix = "wiki.auth")
public record AuthProperties(
        @NotBlank(message = "WIKI_AUTH_ISSUER가 비어 있습니다") String issuer,
        @NotEmpty(message = "WIKI_AUTH_RESOURCES가 비어 있습니다") List<@NotBlank String> resources,
        String signingKey,
        boolean cimdAllowHttpLocalhost,
        List<@NotNull @Valid Client> clients) {

    public AuthProperties {
        clients = clients == null ? List.of() : clients;
    }

    /**
     * 사전 등록 클라이언트. secret-hash가 없으면 공개 클라이언트(none), 있으면 기밀 클라이언트(client_secret_basic)다.
     * 설정에는 비밀의 bcrypt 해시만 둔다. 등급은 빼면 external이다.
     */
    public record Client(
            @NotBlank String id,
            @NotEmpty List<@NotBlank String> redirectUris,
            @Pattern(regexp = "^\\$2[aby]\\$\\d{2}\\$[./A-Za-z0-9]{53}$",
                    message = "secret-hash는 bcrypt 해시여야 합니다. 비밀 원문을 넣지 마세요") String secretHash,
            ClientTier tier) {

        public Client {
            tier = tier == null ? ClientTier.EXTERNAL : tier;
        }

        public boolean isConfidential() {
            return secretHash != null;
        }

        /**
         * 공개 클라이언트의 id는 비밀이 아니어서 다른 앱도 같은 id로 인가 흐름을 시작할 수 있다(RFC 8252 8.6). 그런
         * 클라이언트가 사내 등급을 받으면 사용자가 동의만 해도 아무 앱이 기밀 문서 본문을 받게 된다.
         */
        @AssertTrue(message = "공개 클라이언트(secret-hash 없음)는 tier를 internal로 둘 수 없습니다")
        public boolean isInternalOnlyWhenConfidential() {
            return tier != ClientTier.INTERNAL || isConfidential();
        }
    }

    public boolean isProduction() {
        return issuer != null && issuer.startsWith("https://");
    }

    /** MCP 서버는 iss를 설정값과 끝의 '/'까지 정확히 비교한다. 메타데이터와 토큰의 발급자가 어긋나지 않게 막는다. */
    @AssertTrue(message = "WIKI_AUTH_ISSUER 끝에 '/'를 붙이지 마세요")
    public boolean isIssuerWithoutTrailingSlash() {
        return issuer == null || !issuer.endsWith("/");
    }

    @AssertTrue(message = "WIKI_AUTH_ISSUER가 https(운영)인데 WIKI_AUTH_SIGNING_KEY가 비어 있습니다")
    public boolean isSigningKeySetInProduction() {
        return !isProduction() || StringUtils.hasText(signingKey);
    }

    @AssertTrue(message = "WIKI_AUTH_CIMD_ALLOW_HTTP_LOCALHOST는 로컬 점검용입니다. WIKI_AUTH_ISSUER가 https(운영)면 켤 수 없습니다")
    public boolean isHttpLocalhostCimdOnlyLocal() {
        return !isProduction() || !cimdAllowHttpLocalhost;
    }
}
