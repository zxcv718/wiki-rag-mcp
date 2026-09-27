package io.github.zxcv718.wiki.web;

import io.github.zxcv718.wiki.config.WikiProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.server.PathContainer;
import org.springframework.http.server.RequestPath;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * 정적 bearer 토큰 두 개로 요청을 막는다. /admin/**은 관리자 토큰, 나머지는 모두 서비스 토큰을 요구한다.
 *
 * 공개 경로가 없으므로 관리자 경로가 아니면 서비스 토큰을 요구해, 경로를 새로 만들 때 인증을 빠뜨려도 열리지 않게
 * 한다. 두 토큰은 서로의 경로에서 통하지 않는다. 검색 서버가 가진 서비스 토큰이 새도 문서를 고칠 수는 없다.
 * Spring Security를 쓰지 않은 이유는 규칙이 이 두 줄뿐이라서다. 사용자 인증(OAuth)은 M5에서 검색 서버 쪽에 붙인다.
 */
@Component
public class TokenAuthFilter extends OncePerRequestFilter {

    private static final String BEARER = "Bearer ";

    private final byte[] serviceToken;
    private final byte[] adminToken;

    public TokenAuthFilter(WikiProperties properties) {
        this.serviceToken = properties.serviceToken().getBytes(StandardCharsets.UTF_8);
        this.adminToken = properties.adminToken().getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        byte[] expected = isAdminPath(request) ? adminToken : serviceToken;
        if (!matches(request.getHeader(HttpHeaders.AUTHORIZATION), expected)) {
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setHeader(HttpHeaders.WWW_AUTHENTICATE, "Bearer");
            return;
        }
        chain.doFilter(request, response);
    }

    /**
     * 경로의 첫 세그먼트가 admin인가. Spring MVC가 핸들러를 고를 때와 같은 방식(세그먼트를 디코딩하고 ';' 매개변수를
     * 뗀 값)으로 판단한다. 원본 URI 문자열의 접두사로 판단하면 /admin;x=1/import 같은 요청이 서비스 토큰으로 관리자
     * API에 닿는다.
     */
    static boolean isAdminPath(HttpServletRequest request) {
        PathContainer path = RequestPath.parse(request.getRequestURI(), request.getContextPath())
                .pathWithinApplication();
        for (PathContainer.Element element : path.elements()) {
            if (element instanceof PathContainer.PathSegment segment) {
                return segment.valueToMatch().equals("admin");
            }
        }
        return false;
    }

    private static boolean matches(String header, byte[] expected) {
        if (header == null || !header.regionMatches(true, 0, BEARER, 0, BEARER.length())) {
            return false;
        }
        byte[] given = header.substring(BEARER.length()).strip().getBytes(StandardCharsets.UTF_8);
        // 비교 시간이 앞에서부터 몇 글자가 맞았는지에 따라 달라지지 않게 한다. 걸리는 시간은 첫 인자 길이에만
        // 비례하므로, 비밀인 토큰 길이 대신 요청이 보낸 값을 앞에 둔다
        return MessageDigest.isEqual(given, expected);
    }
}
