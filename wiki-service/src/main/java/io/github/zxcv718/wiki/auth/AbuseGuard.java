package io.github.zxcv718.wiki.auth;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSessionEvent;
import jakarta.servlet.http.HttpSessionListener;
import java.io.IOException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.security.web.authentication.AuthenticationFailureHandler;
import org.springframework.security.web.authentication.SimpleUrlAuthenticationFailureHandler;
import org.springframework.security.web.servlet.util.matcher.PathPatternRequestMatcher;
import org.springframework.security.web.util.matcher.OrRequestMatcher;
import org.springframework.security.web.util.matcher.RequestMatcher;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * 인가 서버의 남용 방지 (README "남용 방지"). 인가 서버 필터 체인의 맨 앞에서 돈다.
 *
 * - 로그인 실패는 같은 IP에서 1분에 10번, 같은 사용자 id에 1분에 10번을 넘으면 로그인 요청을 429로 거절한다. 사용자 id로도
 *   세는 이유는 IP를 바꿔 가며 한 사람의 비밀번호를 맞히는 시도를 막기 위해서다.
 * - 인가 요청은 같은 IP에서 1분에 60번까지다. 인가 요청마다 세션이 생기고 CIMD 문서를 가져올 수 있기 때문이다.
 * - 세션은 로그인 전 요청에도 생기므로(돌아갈 요청을 기억해야 함) 수에 상한을 둔다. 상한을 넘으면 새 세션이 생길 화면
 *   요청을 503으로 거절한다. 세션이 없는 토큰·메타데이터 요청은 막지 않는다.
 *
 * IP는 request.getRemoteAddr()다. 운영에서는 Tomcat이 내부망 프록시(Caddy)가 넣은 X-Forwarded-For만 믿고 그 값으로
 * 바꿔 준다(application.yml의 forward-headers-strategy). 인터넷에서 직접 보낸 X-Forwarded-For는 무시된다.
 */
final class AbuseGuard extends OncePerRequestFilter {

    static final int LOGIN_FAILURES_PER_MINUTE = 10;
    static final int AUTHORIZE_PER_MINUTE = 60;
    /** 세션 하나는 돌아갈 요청과 CSRF 토큰 정도라 수 KB다. 서버 메모리(ADR-23) 안에서 넉넉한 값으로 정했다. */
    static final int MAX_SESSIONS = 10_000;
    /** 한도마다 기억하는 키(IP, 사용자 id)의 수. 이만큼 차면 오래된 창을 비우고, 그래도 차 있으면 새 키는 세지 않는다. */
    private static final int MAX_KEYS = 10_000;
    private static final Duration WINDOW = Duration.ofMinutes(1);

    private static final RequestMatcher AUTHORIZE = matcher(HttpMethod.GET, "/oauth2/authorize");
    private static final RequestMatcher LOGIN = matcher(HttpMethod.POST, AuthorizationServerConfig.LOGIN);
    /** 세션을 만들 수 있는 화면 요청 */
    private static final RequestMatcher SESSION_PATHS = new OrRequestMatcher(AUTHORIZE, LOGIN,
            matcher(HttpMethod.GET, AuthorizationServerConfig.LOGIN),
            matcher(HttpMethod.GET, AuthorizationServerConfig.CONSENT),
            matcher(HttpMethod.POST, AuthorizationServerConfig.CONSENT));

    private final RateLimiter loginFailuresByIp;
    private final RateLimiter loginFailuresByUser;
    private final RateLimiter authorizeByIp;
    private final SessionCounter sessions;

    AbuseGuard(SessionCounter sessions, Clock clock) {
        this.sessions = sessions;
        this.loginFailuresByIp = new RateLimiter(LOGIN_FAILURES_PER_MINUTE, WINDOW, MAX_KEYS, clock);
        this.loginFailuresByUser = new RateLimiter(LOGIN_FAILURES_PER_MINUTE, WINDOW, MAX_KEYS, clock);
        this.authorizeByIp = new RateLimiter(AUTHORIZE_PER_MINUTE, WINDOW, MAX_KEYS, clock);
    }

    private static RequestMatcher matcher(HttpMethod method, String path) {
        return PathPatternRequestMatcher.withDefaults().matcher(method, path);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (SESSION_PATHS.matches(request) && request.getSession(false) == null
                && sessions.active() >= MAX_SESSIONS) {
            reject(response, HttpStatus.SERVICE_UNAVAILABLE);
            return;
        }
        String ip = request.getRemoteAddr();
        if (AUTHORIZE.matches(request) && !authorizeByIp.tryAcquire(ip)) {
            reject(response, HttpStatus.TOO_MANY_REQUESTS);
            return;
        }
        if (LOGIN.matches(request) && (loginFailuresByIp.isLimited(ip)
                || loginFailuresByUser.isLimited(String.valueOf(request.getParameter("username"))))) {
            reject(response, HttpStatus.TOO_MANY_REQUESTS);
            return;
        }
        chain.doFilter(request, response);
    }

    /** 로그인 실패를 세고 로그인 화면으로 돌려보낸다(폼 로그인의 실패 처리). */
    AuthenticationFailureHandler loginFailureHandler() {
        SimpleUrlAuthenticationFailureHandler redirect =
                new SimpleUrlAuthenticationFailureHandler(AuthorizationServerConfig.LOGIN + "?error");
        return (request, response, exception) -> {
            loginFailuresByIp.record(request.getRemoteAddr());
            loginFailuresByUser.record(String.valueOf(request.getParameter("username")));
            redirect.onAuthenticationFailure(request, response, exception);
        };
    }

    private static void reject(HttpServletResponse response, HttpStatus status) throws IOException {
        response.setHeader("Retry-After", String.valueOf(WINDOW.toSeconds()));
        if (status == HttpStatus.TOO_MANY_REQUESTS) {
            AuthHtml.sendError(response, status, "시도가 너무 많습니다", "1분 뒤에 다시 시도하세요.", null);
        } else {
            AuthHtml.sendError(response, status, "지금은 새 로그인을 받을 수 없습니다", "잠시 뒤에 다시 시도하세요.", null);
        }
    }

    /**
     * 키마다 고정된 1분 창 안의 횟수를 센다.
     * ponytail: 전역 잠금 하나와 고정 창이라 창 경계에서는 짧게 두 배까지 통과한다. 위키를 여러 대로 늘리면 Redis로 옮긴다.
     */
    static final class RateLimiter {

        private final int limit;
        private final Duration window;
        private final int maxKeys;
        private final Clock clock;
        private final Map<String, Window> windows = new HashMap<>();

        private static final class Window {
            private final Instant start;
            private int count;

            Window(Instant start) {
                this.start = start;
            }
        }

        RateLimiter(int limit, Duration window, int maxKeys, Clock clock) {
            this.limit = limit;
            this.window = window;
            this.maxKeys = maxKeys;
            this.clock = clock;
        }

        synchronized boolean isLimited(String key) {
            Window current = current(key, false);
            return current != null && current.count >= limit;
        }

        synchronized void record(String key) {
            Window current = current(key, true);
            if (current != null) {
                current.count++;
            }
        }

        /** 한도 안이면 한 번을 세고 true. */
        synchronized boolean tryAcquire(String key) {
            if (isLimited(key)) {
                return false;
            }
            record(key);
            return true;
        }

        private Window current(String key, boolean create) {
            Instant now = clock.instant();
            Window current = windows.get(key);
            if (current != null && !now.isBefore(current.start.plus(window))) {
                windows.remove(key);
                current = null;
            }
            if (current == null && create) {
                if (windows.size() >= maxKeys) {
                    windows.values().removeIf(w -> !now.isBefore(w.start.plus(window)));
                }
                // 1분 안에 키가 이만큼 새로 생기면 새 키는 세지 않는다. 기억할 수 있는 양에 상한을 두기 위해서다
                if (windows.size() >= maxKeys) {
                    return null;
                }
                current = new Window(now);
                windows.put(key, current);
            }
            return current;
        }
    }

    /** 살아 있는 세션 수. 서블릿 컨테이너가 세션을 만들고 없앨 때 알려 준다(빈으로 두면 Spring Boot가 등록한다). */
    static final class SessionCounter implements HttpSessionListener {

        private final AtomicInteger active = new AtomicInteger();

        @Override
        public void sessionCreated(HttpSessionEvent event) {
            active.incrementAndGet();
        }

        @Override
        public void sessionDestroyed(HttpSessionEvent event) {
            active.decrementAndGet();
        }

        int active() {
            return active.get();
        }
    }
}
