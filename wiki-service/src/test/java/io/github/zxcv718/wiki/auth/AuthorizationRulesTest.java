package io.github.zxcv718.wiki.auth;

import static io.github.zxcv718.wiki.auth.AuthorizationRules.redirectUriMatches;
import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

/** redirect_uri는 정확히 같아야 하고, http 루프백 주소만 포트를 무시한다 (RFC 8252 7.3). */
class AuthorizationRulesTest {

    @Test
    void exactMatchIsAccepted() {
        assertThat(redirectUriMatches("https://claude.ai/api/mcp/auth_callback", "https://claude.ai/api/mcp/auth_callback"))
                .isTrue();
    }

    @Test
    void loopbackIgnoresOnlyThePort() {
        assertThat(redirectUriMatches("http://localhost/callback", "http://localhost:53682/callback")).isTrue();
        assertThat(redirectUriMatches("http://127.0.0.1/callback", "http://127.0.0.1:43210/callback")).isTrue();
        assertThat(redirectUriMatches("http://[::1]/callback", "http://[::1]:43210/callback")).isTrue();
        assertThat(redirectUriMatches("http://localhost:3000/callback", "http://localhost:4000/callback")).isTrue();

        assertThat(redirectUriMatches("http://localhost/callback", "http://localhost:53682/other")).isFalse();
        assertThat(redirectUriMatches("http://localhost/callback", "http://localhost:53682/callback?x=1")).isFalse();
        assertThat(redirectUriMatches("http://localhost/callback", "http://127.0.0.1:53682/callback")).isFalse();
    }

    @Test
    void nonLoopbackPortMustMatch() {
        assertThat(redirectUriMatches("https://app.example/callback", "https://app.example:8443/callback")).isFalse();
        assertThat(redirectUriMatches("https://localhost/callback", "https://localhost:8443/callback")).isFalse();
        assertThat(redirectUriMatches("http://localhost/callback", "http://localhost.app.example:80/callback")).isFalse();
        assertThat(redirectUriMatches("http://localhost/callback", "http://localhost:1@app.example/callback")).isFalse();
    }
}
