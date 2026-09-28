package io.github.zxcv718.wiki.auth;

import static org.assertj.core.api.Assertions.assertThat;

import io.github.zxcv718.wiki.auth.AbuseGuard.RateLimiter;
import io.github.zxcv718.wiki.auth.AbuseGuard.SessionCounter;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.mock.web.MockHttpSession;

/** 세션 상한과 요청 수 제한 (README "남용 방지"). 로그인·인가 요청의 한도는 AuthorizationServerTest가 흐름으로 확인한다. */
class AbuseGuardTest {

    private final MovingClock clock = new MovingClock();

    @Test
    void limiterCountsWithinTheWindowAndForgetsAfterIt() {
        RateLimiter limiter = new RateLimiter(3, Duration.ofMinutes(1), 100, clock);
        for (int i = 0; i < 3; i++) {
            assertThat(limiter.tryAcquire("a")).isTrue();
        }
        assertThat(limiter.tryAcquire("a")).isFalse();
        assertThat(limiter.tryAcquire("b")).isTrue();

        clock.now = clock.now.plus(Duration.ofMinutes(1));
        assertThat(limiter.isLimited("a")).isFalse();
        assertThat(limiter.tryAcquire("a")).isTrue();
    }

    /** 기억할 키 수에 상한이 있다. 가득 차면 지난 창부터 비우고, 그래도 차 있으면 새 키는 세지 않는다. */
    @Test
    void limiterKeepsAtMostMaxKeys() {
        RateLimiter limiter = new RateLimiter(1, Duration.ofMinutes(1), 2, clock);
        limiter.record("a");
        limiter.record("b");
        limiter.record("c");

        assertThat(limiter.isLimited("a")).isTrue();
        assertThat(limiter.isLimited("b")).isTrue();
        assertThat(limiter.isLimited("c")).isFalse();

        clock.now = clock.now.plus(Duration.ofMinutes(1));
        limiter.record("c");
        assertThat(limiter.isLimited("c")).isTrue();
    }

    @Test
    void sessionCapRejectsNewSessionsOnScreensOnly() throws Exception {
        SessionCounter sessions = new SessionCounter();
        for (int i = 0; i < AbuseGuard.MAX_SESSIONS; i++) {
            sessions.sessionCreated(null);
        }
        AbuseGuard guard = new AbuseGuard(sessions, clock);

        // 세션이 없는 화면 요청은 새 세션을 만들 수 있어 거절한다
        MockHttpServletResponse rejected = run(guard, new MockHttpServletRequest("GET", "/login"));
        assertThat(rejected.getStatus()).isEqualTo(503);
        assertThat(rejected.getHeader("Retry-After")).isEqualTo("60");
        assertThat(run(guard, new MockHttpServletRequest("GET", "/oauth2/authorize")).getStatus()).isEqualTo(503);

        // 이미 세션이 있는 사용자, 세션을 쓰지 않는 토큰·공개 키 요청은 통과한다
        MockHttpServletRequest withSession = new MockHttpServletRequest("GET", "/oauth2/consent");
        withSession.setSession(new MockHttpSession());
        assertThat(run(guard, withSession).getStatus()).isEqualTo(200);
        assertThat(run(guard, new MockHttpServletRequest("POST", "/oauth2/token")).getStatus()).isEqualTo(200);
        assertThat(run(guard, new MockHttpServletRequest("GET", "/oauth2/jwks")).getStatus()).isEqualTo(200);

        sessions.sessionDestroyed(null);
        assertThat(run(guard, new MockHttpServletRequest("GET", "/login")).getStatus()).isEqualTo(200);
    }

    private static MockHttpServletResponse run(AbuseGuard guard, MockHttpServletRequest request) throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();
        guard.doFilter(request, response, new MockFilterChain());
        return response;
    }

    private static final class MovingClock extends Clock {

        private Instant now = Instant.parse("2026-09-28T00:00:00Z");

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }

        @Override
        public Instant instant() {
            return now;
        }
    }
}
