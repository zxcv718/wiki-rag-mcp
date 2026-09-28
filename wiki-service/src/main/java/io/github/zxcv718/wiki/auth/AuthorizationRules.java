package io.github.zxcv718.wiki.auth;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Consumer;
import java.util.function.Predicate;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.oauth2.core.AuthorizationGrantType;
import org.springframework.security.oauth2.core.ClientAuthenticationMethod;
import org.springframework.security.oauth2.core.OAuth2AuthenticationException;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2ErrorCodes;
import org.springframework.security.oauth2.core.endpoint.OAuth2AuthorizationRequest;
import org.springframework.security.oauth2.core.endpoint.OAuth2ParameterNames;
import org.springframework.security.oauth2.server.authorization.OAuth2Authorization;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationConsent;
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType;
import org.springframework.security.oauth2.server.authorization.authentication.OAuth2AuthorizationCodeRequestAuthenticationContext;
import org.springframework.security.oauth2.server.authorization.authentication.OAuth2AuthorizationCodeRequestAuthenticationException;
import org.springframework.security.oauth2.server.authorization.authentication.OAuth2AuthorizationCodeRequestAuthenticationToken;
import org.springframework.security.oauth2.server.authorization.authentication.OAuth2AuthorizationCodeRequestAuthenticationValidator;
import org.springframework.security.oauth2.server.authorization.authentication.OAuth2AuthorizationGrantAuthenticationToken;
import org.springframework.security.oauth2.server.authorization.authentication.OAuth2ClientAuthenticationToken;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClient;
import org.springframework.security.oauth2.server.authorization.token.JwtEncodingContext;
import org.springframework.security.web.DefaultRedirectStrategy;
import org.springframework.security.web.RedirectStrategy;
import org.springframework.security.web.authentication.AuthenticationConverter;
import org.springframework.util.StringUtils;
import org.springframework.web.util.UriComponents;
import org.springframework.web.util.UriComponentsBuilder;
import org.springframework.web.util.UriUtils;

/**
 * Spring 인가 서버의 기본 동작을 README "인가 서버"의 계약에 맞추는 규칙. 기본 동작과 다른 것만 여기 둔다.
 *
 * - redirect_uri: 등록한 값과 정확히 같아야 하고, http 루프백 주소만 포트를 무시한다(RFC 8252 7.3). 기본 검사는
 *   localhost를 루프백으로 보지 않아, 세션마다 localhost의 다른 포트를 쓰는 클라이언트(Claude Code)가 붙지 못한다.
 * - resource(RFC 8707): 인가 요청과 코드 교환에 있어야 하고 WIKI_AUTH_RESOURCES 중 하나와 정확히 같아야 한다. 토큰의
 *   aud는 그 값 하나다. Spring 인가 서버는 resource를 몰라 aud에 클라이언트 id를 넣는다.
 * - 인가 응답의 iss(RFC 9207): 성공과 오류 응답 모두에 발급자를 붙여, 클라이언트가 다른 인가 서버의 응답과 섞이지 않게
 *   한다(mix-up 공격). 기본 응답에는 없다.
 * - 로그인 전의 CIMD 인가 오류는 redirect_uri로 돌려보내지 않는다. 누구나 CIMD 문서에 아무 주소나 적을 수 있어, 돌려보내면
 *   이 인가 서버가 피싱 사이트로 보내는 링크가 된다(RFC 9700 4.11.2).
 * - client_tier: 설정이 internal이고 이번 토큰 요청에서 비밀로 인증한 클라이언트만 internal이다.
 */
final class AuthorizationRules {

    static final String SCOPE = "wiki:read";
    static final String RESOURCE = "resource";
    static final String INVALID_TARGET = "invalid_target";
    static final String CLIENT_ID_CLAIM = "client_id";
    static final String CLIENT_TIER_CLAIM = "client_tier";

    private static final Set<String> LOOPBACK_HOSTS = Set.of("localhost", "127.0.0.1", "[::1]");

    private final String issuer;
    private final Set<String> resources;
    private final Predicate<String> preRegistered;
    private final RedirectStrategy redirects = new DefaultRedirectStrategy();

    AuthorizationRules(String issuer, List<String> resources, Predicate<String> preRegistered) {
        this.issuer = issuer;
        this.resources = Set.copyOf(resources);
        this.preRegistered = preRegistered;
    }

    /** 인가 요청 검사. redirect_uri를 먼저 본다. 확인하지 못한 주소로는 오류 응답도 보내지 않기 때문이다. */
    Consumer<OAuth2AuthorizationCodeRequestAuthenticationContext> authorizationRequestValidator() {
        Consumer<OAuth2AuthorizationCodeRequestAuthenticationContext> redirectUri = AuthorizationRules::validateRedirectUri;
        return redirectUri
                .andThen(OAuth2AuthorizationCodeRequestAuthenticationValidator.DEFAULT_SCOPE_VALIDATOR)
                .andThen(this::validateResource);
    }

    /**
     * scope를 빼면 wiki:read로 본다(RFC 6749 3.3의 기본값). 스코프가 이것 하나뿐이라, 빠진 요청을 거절하기보다 기본값으로
     * 받는 편이 스코프를 보내지 않는 클라이언트와도 붙는다.
     */
    static AuthenticationConverter defaultScope(AuthenticationConverter converter) {
        return request -> {
            Authentication converted = converter.convert(request);
            if (converted instanceof OAuth2AuthorizationCodeRequestAuthenticationToken token
                    && token.getScopes().isEmpty()) {
                return new OAuth2AuthorizationCodeRequestAuthenticationToken(token.getAuthorizationUri(),
                        token.getClientId(), (Authentication) token.getPrincipal(), token.getRedirectUri(),
                        token.getState(), Set.of(SCOPE), token.getAdditionalParameters());
            }
            return converted;
        };
    }

    /**
     * 이번 인가에 동의 화면을 보일지. 공개 클라이언트는 이전에 허용했어도 매번 보인다(RFC 8252 8.6). client_id는 누구나
     * 쓸 수 있어서, 동의를 기억하면 로그인 세션이 살아 있는 동안 같은 id를 쓴 다른 프로그램이 화면 없이 코드를 받아 간다.
     * 비밀로 인증하는 기밀 클라이언트는 이미 허용한 범위면 건너뛴다(Spring 기본 동작과 같다).
     */
    static boolean consentRequired(OAuth2AuthorizationCodeRequestAuthenticationContext context) {
        RegisteredClient client = context.getRegisteredClient();
        if (!client.getClientSettings().isRequireAuthorizationConsent()) {
            return false;
        }
        if (client.getClientAuthenticationMethods().contains(ClientAuthenticationMethod.NONE)) {
            return true;
        }
        OAuth2AuthorizationConsent consent = context.getAuthorizationConsent();
        return consent == null || !consent.getScopes().containsAll(context.getAuthorizationRequest().getScopes());
    }

    /** 등록한 주소와 같은가. http 루프백 주소만 포트를 무시하고 나머지(경로, 쿼리)는 정확히 같아야 한다. */
    static boolean redirectUriMatches(String registered, String requested) {
        if (registered.equals(requested)) {
            return true;
        }
        UriComponents uri;
        try {
            uri = UriComponentsBuilder.fromUriString(requested).build();
        } catch (IllegalArgumentException e) {
            return false;
        }
        if (!isLoopback(uri)) {
            return false;
        }
        return UriComponentsBuilder.fromUriString(registered).port(uri.getPort()).build().toUriString()
                .equals(requested);
    }

    /** http 루프백 주소인가(RFC 8252 7.3). 이 주소로 돌아가는 코드는 사용자의 컴퓨터에서 실행 중인 앱만 받는다. */
    static boolean isLoopback(UriComponents uri) {
        return "http".equals(uri.getScheme()) && LOOPBACK_HOSTS.contains(uri.getHost());
    }

    private static void validateRedirectUri(OAuth2AuthorizationCodeRequestAuthenticationContext context) {
        OAuth2AuthorizationCodeRequestAuthenticationToken request = context.getAuthentication();
        Set<String> registered = context.getRegisteredClient().getRedirectUris();
        String requested = request.getRedirectUri();
        // 빼는 것은 등록한 주소가 하나일 때만 허용한다(OAuth 2.1). Spring 기본 검사와 같다
        boolean valid = StringUtils.hasText(requested)
                ? registered.stream().anyMatch(uri -> redirectUriMatches(uri, requested))
                : registered.size() == 1;
        if (!valid) {
            throw authorizationError(OAuth2ErrorCodes.INVALID_REQUEST, OAuth2ParameterNames.REDIRECT_URI, request, null);
        }
    }

    private void validateResource(OAuth2AuthorizationCodeRequestAuthenticationContext context) {
        OAuth2AuthorizationCodeRequestAuthenticationToken request = context.getAuthentication();
        // 여러 번 보내면 배열로 들어온다. 토큰 하나에 대상 하나만 두므로 받지 않는다
        if (request.getAdditionalParameters().get(RESOURCE) instanceof String resource && resources.contains(resource)) {
            return;
        }
        String redirectUri = StringUtils.hasText(request.getRedirectUri())
                ? request.getRedirectUri()
                : context.getRegisteredClient().getRedirectUris().iterator().next();
        throw authorizationError(INVALID_TARGET, RESOURCE, request, redirectUri);
    }

    /** redirectUri가 null이면 클라이언트로 돌려보내지 않고 400으로 끝난다. */
    private static OAuth2AuthorizationCodeRequestAuthenticationException authorizationError(
            String code, String parameter, OAuth2AuthorizationCodeRequestAuthenticationToken request, String redirectUri) {
        OAuth2AuthorizationCodeRequestAuthenticationToken result = new OAuth2AuthorizationCodeRequestAuthenticationToken(
                request.getAuthorizationUri(), request.getClientId(), (Authentication) request.getPrincipal(),
                redirectUri, request.getState(), request.getScopes(), request.getAdditionalParameters());
        return new OAuth2AuthorizationCodeRequestAuthenticationException(
                new OAuth2Error(code, "OAuth 2.0 Parameter: " + parameter, null), result);
    }

    /**
     * 액세스 토큰의 클레임을 계약에 맞춘다. aud는 인가 때의 resource 하나, client_id는 클라이언트 id(CIMD는 문서 주소),
     * client_tier는 신뢰 등급, scope는 공백으로 구분한 문자열(RFC 9068)이다. sub(위키 사용자 id), iss, exp, iat, jti는
     * 기본값 그대로 쓴다.
     */
    void customize(JwtEncodingContext context) {
        if (!OAuth2TokenType.ACCESS_TOKEN.equals(context.getTokenType())) {
            return;
        }
        // 클레임은 인가 기록과 함께 DB에 JSON으로 저장되고, 읽을 때 허용된 컬렉션 형식만 되살린다. List.of는 허용 목록에
        // 없어 Spring 기본과 같은 singletonList를 쓴다
        context.getClaims()
                .audience(Collections.singletonList(grantedResource(context)))
                .claim(CLIENT_ID_CLAIM, context.getRegisteredClient().getClientId())
                .claim(CLIENT_TIER_CLAIM, tier(context).claimValue())
                .claim(OAuth2ParameterNames.SCOPE, String.join(" ", new TreeSet<>(context.getAuthorizedScopes())));
    }

    /**
     * 설정이 internal이어도 이번 요청에서 비밀로 인증하지 않았으면 external이다. 설정 검증(AuthProperties)이 공개
     * 클라이언트의 internal을 막지만, 등급은 토큰 하나로 기밀 문서가 새는 값이라 발급하는 자리에서 한 번 더 본다.
     */
    private static ClientTier tier(JwtEncodingContext context) {
        boolean authenticatedWithSecret = context.getAuthorizationGrant().getPrincipal()
                instanceof OAuth2ClientAuthenticationToken client
                && !ClientAuthenticationMethod.NONE.equals(client.getClientAuthenticationMethod());
        return authenticatedWithSecret && ClientRegistry.tierOf(context.getRegisteredClient()) == ClientTier.INTERNAL
                ? ClientTier.INTERNAL : ClientTier.EXTERNAL;
    }

    /**
     * 인가 때 받은 resource. 코드 교환에서는 같은 값을 다시 보내야 하고, 갱신에서는 빼도 되지만 보내면 같아야 한다.
     * 갱신할 때도 그 값이 지금의 WIKI_AUTH_RESOURCES에 남아 있어야 한다. 설정에서 뺀 리소스용 토큰이 갱신으로 계속
     * 나오면 안 되기 때문이다. 토큰을 만들기 전에 던지므로 거절한 요청으로는 코드가 소모되거나 갱신 토큰이 바뀌지 않는다.
     */
    private String grantedResource(JwtEncodingContext context) {
        OAuth2Authorization authorization = context.getAuthorization();
        OAuth2AuthorizationRequest original = authorization == null ? null
                : authorization.getAttribute(OAuth2AuthorizationRequest.class.getName());
        Object granted = original == null ? null : original.getAdditionalParameters().get(RESOURCE);
        Map<String, Object> parameters =
                ((OAuth2AuthorizationGrantAuthenticationToken) context.getAuthorizationGrant()).getAdditionalParameters();
        Object requested = parameters.get(RESOURCE);
        boolean required = AuthorizationGrantType.AUTHORIZATION_CODE.equals(context.getAuthorizationGrantType());
        if (!(granted instanceof String resource) || !resources.contains(resource) || (requested == null && required)
                || (requested != null && !resource.equals(requested))) {
            throw new OAuth2AuthenticationException(
                    new OAuth2Error(INVALID_TARGET, "OAuth 2.0 Parameter: " + RESOURCE, null));
        }
        return resource;
    }

    /** 인가 성공 응답: code, state, iss를 붙여 redirect_uri로 보낸다. */
    void sendAuthorizationResponse(HttpServletRequest request, HttpServletResponse response,
                                   Authentication authentication) throws IOException {
        OAuth2AuthorizationCodeRequestAuthenticationToken result =
                (OAuth2AuthorizationCodeRequestAuthenticationToken) authentication;
        UriComponentsBuilder uri = UriComponentsBuilder.fromUriString(result.getRedirectUri())
                .queryParam(OAuth2ParameterNames.CODE, result.getAuthorizationCode().getTokenValue());
        redirectWithStateAndIssuer(request, response, uri, result.getState());
    }

    /**
     * 인가 오류 응답. 돌려보낼 주소를 확인하지 못했거나, 로그인 전 CIMD 클라이언트의 오류면 클라이언트로 보내지 않고 400
     * 오류 화면으로 끝낸다. 사전 등록 클라이언트의 돌아갈 주소는 우리가 정한 것이라 돌려보낸다.
     */
    void sendErrorResponse(HttpServletRequest request, HttpServletResponse response,
                           AuthenticationException exception) throws IOException {
        OAuth2Error error = ((OAuth2AuthenticationException) exception).getError();
        OAuth2AuthorizationCodeRequestAuthenticationToken failed =
                exception instanceof OAuth2AuthorizationCodeRequestAuthenticationException e
                        ? e.getAuthorizationCodeRequestAuthentication() : null;
        if (failed == null || !StringUtils.hasText(failed.getRedirectUri())
                || (!preRegistered.test(failed.getClientId()) && !isLoggedIn(failed.getPrincipal()))) {
            response.sendError(HttpStatus.BAD_REQUEST.value(), error.toString());
            return;
        }
        UriComponentsBuilder uri = UriComponentsBuilder.fromUriString(failed.getRedirectUri())
                .queryParam(OAuth2ParameterNames.ERROR, error.getErrorCode());
        if (StringUtils.hasText(error.getDescription())) {
            uri.queryParam(OAuth2ParameterNames.ERROR_DESCRIPTION, encode(error.getDescription()));
        }
        redirectWithStateAndIssuer(request, response, uri, failed.getState());
    }

    private static boolean isLoggedIn(Object principal) {
        return principal instanceof Authentication user && user.isAuthenticated()
                && !(user instanceof AnonymousAuthenticationToken);
    }

    private void redirectWithStateAndIssuer(HttpServletRequest request, HttpServletResponse response,
                                            UriComponentsBuilder uri, String state) throws IOException {
        if (StringUtils.hasText(state)) {
            uri.queryParam(OAuth2ParameterNames.STATE, encode(state));
        }
        uri.queryParam("iss", encode(issuer));
        redirects.sendRedirect(request, response, uri.build(true).toUriString());
    }

    private static String encode(String value) {
        return UriUtils.encode(value, StandardCharsets.UTF_8);
    }
}
