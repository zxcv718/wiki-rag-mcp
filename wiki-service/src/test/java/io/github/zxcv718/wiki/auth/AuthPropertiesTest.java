package io.github.zxcv718.wiki.auth;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

/** 발급자가 https(운영)면 위험한 설정으로는 뜨지 않는다 (fail closed). */
class AuthPropertiesTest {

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(AuthProperties.class)
    static class PropertiesOnly {
    }

    private final ApplicationContextRunner runner = new ApplicationContextRunner()
            .withUserConfiguration(PropertiesOnly.class)
            .withPropertyValues("wiki.auth.resources=https://mcp.dmssh.store/mcp");

    @Test
    void localIssuerStartsWithoutSigningKey() {
        runner.withPropertyValues("wiki.auth.issuer=http://127.0.0.1:8081", "wiki.auth.cimd-allow-http-localhost=true")
                .run(context -> assertThat(context).hasNotFailed());
    }

    @Test
    void productionIssuerNeedsSigningKey() {
        runner.withPropertyValues("wiki.auth.issuer=https://auth.dmssh.store")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("WIKI_AUTH_SIGNING_KEY가 비어 있습니다"));
        runner.withPropertyValues("wiki.auth.issuer=https://auth.dmssh.store", "wiki.auth.signing-key=/run/secrets/key.pem")
                .run(context -> assertThat(context).hasNotFailed());
    }

    /** 켜 두면 누구나 client_id로 서버 자신의 로컬 포트에 요청을 보내게 할 수 있다. */
    @Test
    void productionRejectsHttpLocalhostCimd() {
        runner.withPropertyValues("wiki.auth.issuer=https://auth.dmssh.store", "wiki.auth.signing-key=/run/secrets/key.pem",
                        "wiki.auth.cimd-allow-http-localhost=true")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("WIKI_AUTH_CIMD_ALLOW_HTTP_LOCALHOST는 로컬 점검용입니다"));
    }

    @Test
    void issuerWithTrailingSlashIsRejected() {
        runner.withPropertyValues("wiki.auth.issuer=http://127.0.0.1:8081/")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("WIKI_AUTH_ISSUER 끝에 '/'를 붙이지 마세요"));
    }

    /** 공개 클라이언트의 id는 다른 앱도 쓸 수 있어(RFC 8252 8.6) 사내 등급을 줄 수 없다. */
    @Test
    void publicClientCannotBeInternal() {
        runner.withPropertyValues("wiki.auth.issuer=http://127.0.0.1:8081", "wiki.auth.clients[0].id=demo",
                        "wiki.auth.clients[0].redirect-uris=http://127.0.0.1/callback", "wiki.auth.clients[0].tier=internal")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("공개 클라이언트(secret-hash 없음)는 tier를 internal로 둘 수 없습니다"));
    }

    @Test
    void confidentialClientCanBeInternal() {
        String hash = new BCryptPasswordEncoder().encode("test-secret");
        runner.withPropertyValues("wiki.auth.issuer=http://127.0.0.1:8081", "wiki.auth.clients[0].id=demo",
                        "wiki.auth.clients[0].redirect-uris=https://demo.example/callback",
                        "wiki.auth.clients[0].secret-hash=" + hash, "wiki.auth.clients[0].tier=internal")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    AuthProperties.Client client = context.getBean(AuthProperties.class).clients().getFirst();
                    assertThat(client.isConfidential()).isTrue();
                    assertThat(client.tier()).isEqualTo(ClientTier.INTERNAL);
                });
    }

    /** 비밀 원문을 넣는 실수를 막는다. 설정에는 bcrypt 해시만 둔다. */
    @Test
    void secretMustBeBcryptHash() {
        runner.withPropertyValues("wiki.auth.issuer=http://127.0.0.1:8081", "wiki.auth.clients[0].id=demo",
                        "wiki.auth.clients[0].redirect-uris=https://demo.example/callback",
                        "wiki.auth.clients[0].secret-hash=plain-secret")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("secret-hash는 bcrypt 해시여야 합니다"));
    }

    @Test
    void clientTierDefaultsToExternal() {
        runner.withPropertyValues("wiki.auth.issuer=http://127.0.0.1:8081", "wiki.auth.clients[0].id=dev",
                        "wiki.auth.clients[0].redirect-uris=http://127.0.0.1/callback")
                .run(context -> assertThat(context.getBean(AuthProperties.class).clients().getFirst().tier())
                        .isEqualTo(ClientTier.EXTERNAL));
    }

    @Test
    void emptyResourcesAreRejected() {
        runner.withPropertyValues("wiki.auth.issuer=http://127.0.0.1:8081", "wiki.auth.resources=")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("WIKI_AUTH_RESOURCES가 비어 있습니다"));
    }
}
