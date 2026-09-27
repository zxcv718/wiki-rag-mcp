package io.github.zxcv718.wiki.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

/** 토큰이 비어 있으면 서비스가 뜨지 않아야 한다 (fail closed). */
class WikiPropertiesTest {

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(WikiProperties.class)
    static class PropertiesOnly {
    }

    private final ApplicationContextRunner runner = new ApplicationContextRunner()
            .withUserConfiguration(PropertiesOnly.class)
            .withPropertyValues("wiki.public-url=https://wiki.saesol.example", "wiki.event-partitions=4");

    @Test
    void startsWithBothTokens() {
        runner.withPropertyValues("wiki.service-token=s", "wiki.admin-token=a")
                .run(context -> assertThat(context).hasNotFailed());
    }

    @Test
    void failsWithoutServiceToken() {
        runner.withPropertyValues("wiki.service-token=", "wiki.admin-token=a")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("WIKI_SERVICE_TOKEN이 비어 있습니다"));
    }

    @Test
    void failsWithBlankAdminToken() {
        runner.withPropertyValues("wiki.service-token=s", "wiki.admin-token=  ")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("WIKI_ADMIN_TOKEN이 비어 있습니다"));
    }

    /** 같은 토큰이면 서비스 토큰으로 관리자 API를 부를 수 있게 된다. */
    @Test
    void failsWhenTokensAreEqual() {
        runner.withPropertyValues("wiki.service-token=same", "wiki.admin-token=same")
                .run(context -> assertThat(context.getStartupFailure())
                        .hasStackTraceContaining("WIKI_SERVICE_TOKEN과 WIKI_ADMIN_TOKEN이 같습니다"));
    }

    @Test
    void documentUrlIgnoresTrailingSlash() {
        WikiProperties properties = new WikiProperties("s", "a", "https://wiki.saesol.example/", 4);
        assertThat(properties.documentUrl("infra-011")).isEqualTo("https://wiki.saesol.example/doc/infra-011");
    }
}
