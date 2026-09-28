package io.github.zxcv718.wiki.auth;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.KeyUse;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.proc.SecurityContext;
import io.github.zxcv718.wiki.domain.UserRepository;
import jakarta.servlet.Filter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.KeyPairGenerator;
import java.security.KeyPair;
import java.security.interfaces.RSAPrivateCrtKey;
import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.RSAPublicKeySpec;
import java.time.Clock;
import java.time.Instant;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.AuthenticationProvider;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.converter.RsaKeyConverters;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.keygen.Base64StringKeyGenerator;
import org.springframework.security.crypto.keygen.StringKeyGenerator;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.core.AuthorizationGrantType;
import org.springframework.security.oauth2.core.ClientAuthenticationMethod;
import org.springframework.security.oauth2.core.OAuth2AuthenticationException;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2ErrorCodes;
import org.springframework.security.oauth2.core.OAuth2RefreshToken;
import org.springframework.security.oauth2.core.OAuth2Token;
import org.springframework.security.oauth2.core.endpoint.OAuth2ParameterNames;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.security.oauth2.server.authorization.JdbcOAuth2AuthorizationConsentService;
import org.springframework.security.oauth2.server.authorization.JdbcOAuth2AuthorizationService;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationConsentService;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationServerMetadata;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationService;
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType;
import org.springframework.security.oauth2.server.authorization.authentication.OAuth2AuthorizationCodeRequestAuthenticationProvider;
import org.springframework.security.oauth2.server.authorization.authentication.OAuth2ClientAuthenticationToken;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClient;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClientRepository;
import org.springframework.security.oauth2.server.authorization.settings.AuthorizationServerSettings;
import org.springframework.security.oauth2.server.authorization.token.DelegatingOAuth2TokenGenerator;
import org.springframework.security.oauth2.server.authorization.token.JwtGenerator;
import org.springframework.security.oauth2.server.authorization.token.OAuth2TokenContext;
import org.springframework.security.oauth2.server.authorization.token.OAuth2TokenGenerator;
import org.springframework.security.oauth2.server.authorization.web.OAuth2AuthorizationEndpointFilter;
import org.springframework.security.oauth2.server.authorization.web.authentication.OAuth2AuthorizationCodeRequestAuthenticationConverter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.intercept.AuthorizationFilter;
import org.springframework.security.web.authentication.LoginUrlAuthenticationEntryPoint;
import org.springframework.security.web.session.DisableEncodeUrlFilter;
import org.springframework.security.web.firewall.RequestRejectedHandler;
import org.springframework.security.web.servlet.util.matcher.PathPatternRequestMatcher;
import org.springframework.security.web.util.matcher.OrRequestMatcher;
import org.springframework.security.web.util.matcher.RequestMatcher;
import org.springframework.util.StringUtils;

/**
 * OAuth 2.1 인가 서버 (README "인가 서버", ADR-24). Spring Security의 인가 서버에 계약과 다른 부분(AuthorizationRules,
 * ClientRegistry)을 끼운다.
 *
 * 필터 체인은 공개 경로만 맡는다. 그 밖의 경로는 이 체인을 타지 않고 TokenAuthFilter가 서비스·관리자 토큰으로 막는다.
 * 그래서 Spring 인가 서버가 기본으로 여는 엔드포인트(토큰 폐기·조회, OIDC, 기기 인가)도 공개 경로에 없으면 열리지 않는다.
 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(AuthProperties.class)
public class AuthorizationServerConfig {

    static final String LOGIN = "/login";
    static final String CONSENT = "/oauth2/consent";
    private static final String AUTHORIZE = "/oauth2/authorize";

    /** 토큰 없이 열리는 경로 (README "공개 경로"). 인가 서버 체인과 TokenAuthFilter가 이 목록 하나를 같이 쓴다. */
    public static final RequestMatcher PUBLIC_PATHS = new OrRequestMatcher(
            matcher(HttpMethod.GET, "/.well-known/oauth-authorization-server"),
            matcher(HttpMethod.GET, AUTHORIZE),
            matcher(HttpMethod.POST, "/oauth2/token"),
            matcher(HttpMethod.GET, "/oauth2/jwks"),
            matcher(HttpMethod.GET, LOGIN),
            matcher(HttpMethod.POST, LOGIN),
            matcher(HttpMethod.GET, CONSENT),
            matcher(HttpMethod.POST, CONSENT));

    /** 로그인하지 않았으면 로그인 화면으로 보낼 경로. 토큰 엔드포인트는 Spring 기본대로 401이다. */
    private static final RequestMatcher BROWSER_PATHS = new OrRequestMatcher(
            matcher(HttpMethod.GET, AUTHORIZE), matcher(HttpMethod.GET, CONSENT), matcher(HttpMethod.POST, CONSENT));

    /** 동의 제출. response_type이 있으면 인가 요청 모양이라 받지 않는다(POST 인가 요청은 열지 않는다). */
    private static final RequestMatcher CONSENT_POST = matcher(HttpMethod.POST, CONSENT);
    private static final RequestMatcher CONSENT_SUBMISSION = request -> CONSENT_POST.matches(request)
            && request.getParameter(OAuth2ParameterNames.RESPONSE_TYPE) == null;

    private static final StringKeyGenerator REFRESH_TOKENS =
            new Base64StringKeyGenerator(Base64.getUrlEncoder().withoutPadding(), 96);

    /** 로그인·동의 화면의 CSP. 화면에는 스크립트도 외부 자원도 없고, 인라인 스타일만 쓴다. */
    static final String CONTENT_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'";

    private static RequestMatcher matcher(HttpMethod method, String path) {
        return PathPatternRequestMatcher.withDefaults().matcher(method, path);
    }

    @Bean
    SecurityFilterChain authorizationServerChain(HttpSecurity http, AuthorizationRules rules,
                                                 RegisteredClientRepository clients,
                                                 OAuth2TokenGenerator<OAuth2Token> tokenGenerator,
                                                 AuthorizationServerSettings settings,
                                                 OAuth2AuthorizationService authorizations,
                                                 OAuth2AuthorizationConsentService consents,
                                                 AbuseGuard.SessionCounter sessions, Clock clock) {
        AbuseGuard guard = new AbuseGuard(sessions, clock);
        http.securityMatcher(PUBLIC_PATHS)
                .addFilterBefore(guard, DisableEncodeUrlFilter.class)
                .oauth2AuthorizationServer(server -> server
                        .registeredClientRepository(clients)
                        .authorizationServerSettings(settings)
                        .authorizationService(authorizations)
                        .authorizationConsentService(consents)
                        .tokenGenerator(tokenGenerator)
                        .authorizationServerMetadataEndpoint(metadata -> metadata
                                .authorizationServerMetadataCustomizer(AuthorizationServerConfig::metadata))
                        .authorizationEndpoint(authorize -> authorize
                                .consentPage(CONSENT)
                                .authorizationRequestConverters(converters -> converters.replaceAll(converter ->
                                        converter instanceof OAuth2AuthorizationCodeRequestAuthenticationConverter
                                                ? AuthorizationRules.defaultScope(converter) : converter))
                                .authenticationProviders(providers -> providers.forEach(provider -> {
                                    if (provider instanceof OAuth2AuthorizationCodeRequestAuthenticationProvider code) {
                                        code.setAuthenticationValidator(rules.authorizationRequestValidator());
                                        code.setAuthorizationConsentRequired(AuthorizationRules::consentRequired);
                                    }
                                }))
                                .authorizationResponseHandler(rules::sendAuthorizationResponse)
                                .errorResponseHandler(rules::sendErrorResponse))
                        .clientAuthentication(client -> client
                                .authenticationConverter(AuthorizationServerConfig::publicRefreshClient)
                                .authenticationProvider(new PublicRefreshClientAuthentication(clients))))
                .with(new ConsentSubmission(rules), Customizer.withDefaults())
                .authorizeHttpRequests(authorize -> authorize.anyRequest().authenticated())
                .formLogin(login -> login.loginPage(LOGIN).failureHandler(guard.loginFailureHandler()).permitAll())
                .headers(headers -> headers.contentSecurityPolicy(csp -> csp.policyDirectives(CONTENT_SECURITY_POLICY)))
                .exceptionHandling(exceptions -> exceptions.defaultAuthenticationEntryPointFor(
                        new LoginUrlAuthenticationEntryPoint(LOGIN), BROWSER_PATHS));
        return http.build();
    }

    /**
     * 메타데이터를 계약에 맞춘다. 열지 않은 엔드포인트(토큰 폐기·조회)와 쓰지 않는 기능(mTLS, DPoP)은 알리지 않는다.
     * registration_endpoint는 DCR을 켜지 않아 원래 없다.
     */
    private static void metadata(OAuth2AuthorizationServerMetadata.Builder builder) {
        builder.claims(claims -> {
            claims.keySet().removeAll(List.of("revocation_endpoint", "revocation_endpoint_auth_methods_supported",
                    "introspection_endpoint", "introspection_endpoint_auth_methods_supported",
                    "tls_client_certificate_bound_access_tokens", "dpop_signing_alg_values_supported"));
            claims.put("grant_types_supported", List.of(AuthorizationGrantType.AUTHORIZATION_CODE.getValue(),
                    AuthorizationGrantType.REFRESH_TOKEN.getValue()));
            claims.put("token_endpoint_auth_methods_supported", List.of(ClientAuthenticationMethod.NONE.getValue(),
                    ClientAuthenticationMethod.CLIENT_SECRET_BASIC.getValue()));
            claims.put("scopes_supported", List.of(AuthorizationRules.SCOPE));
            // Claude는 이 값과 token_endpoint_auth_methods_supported의 none이 함께 있어야 CIMD를 쓴다 (ADR-24)
            claims.put("client_id_metadata_document_supported", true);
            claims.put("authorization_response_iss_parameter_supported", true);
        });
    }

    @Bean
    AuthorizationRules authorizationRules(AuthProperties properties, ClientRegistry clients) {
        return new AuthorizationRules(properties.issuer(), properties.resources(), clients::isPreRegistered);
    }

    @Bean
    AuthorizationServerSettings authorizationServerSettings(AuthProperties properties) {
        return AuthorizationServerSettings.builder().issuer(properties.issuer()).build();
    }

    @Bean
    ClientMetadataDocuments clientMetadataDocuments(AuthProperties properties, Clock clock) {
        return new ClientMetadataDocuments(host -> List.of(InetAddress.getAllByName(host)), new SocketFetcher(),
                properties.cimdAllowHttpLocalhost(), clock);
    }

    @Bean
    ClientRegistry registeredClientRepository(AuthProperties properties, ClientMetadataDocuments documents) {
        return new ClientRegistry(properties.clients(), documents);
    }

    /** 인가 기록(코드, 토큰)은 위키 DB에 둔다(V3). 다시 시작해도 갱신 토큰이 남고, 재사용 탐지도 여기서 한다. */
    @Bean
    AuthorizationStore authorizationService(JdbcTemplate jdbc, RegisteredClientRepository clients, Clock clock) {
        return new AuthorizationStore(new JdbcOAuth2AuthorizationService(jdbc, clients), jdbc, clock);
    }

    @Bean
    OAuth2AuthorizationConsentService authorizationConsentService(JdbcTemplate jdbc,
                                                                  RegisteredClientRepository clients) {
        return new JdbcOAuth2AuthorizationConsentService(jdbc, clients);
    }

    @Bean
    AbuseGuard.SessionCounter sessionCounter() {
        return new AbuseGuard.SessionCounter();
    }

    /**
     * 토큰 서명 키. WIKI_AUTH_SIGNING_KEY가 없으면(로컬) 시작할 때 새로 만든다. 운영에서 비어 있으면 AuthProperties
     * 검증에서 먼저 막힌다. kid는 공개 키의 JWK 지문(RFC 7638)이라, 같은 키 파일이면 다시 시작해도 kid가 같다.
     */
    @Bean
    JWKSource<SecurityContext> jwkSource(AuthProperties properties) throws IOException, GeneralSecurityException {
        KeyPair keys = StringUtils.hasText(properties.signingKey())
                ? readKeyPair(Path.of(properties.signingKey()))
                : generateKeyPair();
        try {
            RSAKey key = new RSAKey.Builder((RSAPublicKey) keys.getPublic())
                    .privateKey((RSAPrivateKey) keys.getPrivate())
                    .keyUse(KeyUse.SIGNATURE)
                    .algorithm(JWSAlgorithm.RS256)
                    .keyIDFromThumbprint()
                    .build();
            return new ImmutableJWKSet<>(new JWKSet(key));
        } catch (JOSEException e) {
            throw new IllegalStateException("서명 키의 kid를 만들지 못했습니다", e);
        }
    }

    /** PKCS#8 PEM(-----BEGIN PRIVATE KEY-----). 공개 키는 개인 키의 modulus와 공개 지수로 만든다. */
    private static KeyPair readKeyPair(Path path) throws IOException, GeneralSecurityException {
        RSAPrivateKey privateKey;
        try (InputStream in = Files.newInputStream(path)) {
            privateKey = RsaKeyConverters.pkcs8().convert(in);
        }
        if (!(privateKey instanceof RSAPrivateCrtKey crt)) {
            throw new IllegalStateException("WIKI_AUTH_SIGNING_KEY에서 RSA 공개 지수를 읽지 못했습니다: " + path);
        }
        RSAPublicKey publicKey = (RSAPublicKey) KeyFactory.getInstance("RSA")
                .generatePublic(new RSAPublicKeySpec(crt.getModulus(), crt.getPublicExponent()));
        return new KeyPair(publicKey, privateKey);
    }

    private static KeyPair generateKeyPair() throws GeneralSecurityException {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        return generator.generateKeyPair();
    }

    /** 액세스 토큰은 서명한 JWT, 갱신 토큰은 추측할 수 없는 임의 문자열이다. */
    @Bean
    OAuth2TokenGenerator<OAuth2Token> tokenGenerator(JWKSource<SecurityContext> jwkSource, AuthorizationRules rules) {
        JwtGenerator accessTokens = new JwtGenerator(new NimbusJwtEncoder(jwkSource));
        accessTokens.setJwtCustomizer(rules::customize);
        OAuth2TokenGenerator<OAuth2RefreshToken> refreshTokens = AuthorizationServerConfig::refreshToken;
        return new DelegatingOAuth2TokenGenerator(accessTokens, refreshTokens);
    }

    /**
     * Spring 기본 생성기는 공개 클라이언트에 갱신 토큰을 주지 않는다. 계약은 공개 클라이언트에도 주고, 쓸 때마다 새 것으로
     * 바꾼다(클라이언트 설정 reuseRefreshTokens=false). 바꾼 옛 토큰이 다시 오면 AuthorizationStore가 인가 전체를 지운다.
     *
     * 갱신 토큰 하나는 7일이지만 처음 로그인(인가 코드 발급)에서 30일을 넘지 않게 자른다. 30일이 지났으면 발급하지 않고
     * 거절해, 갱신을 이어 가도 30일마다 다시 로그인하게 한다.
     */
    private static OAuth2RefreshToken refreshToken(OAuth2TokenContext context) {
        if (!OAuth2TokenType.REFRESH_TOKEN.equals(context.getTokenType())) {
            return null;
        }
        Instant issuedAt = Instant.now();
        Instant expiresAt = issuedAt.plus(context.getRegisteredClient().getTokenSettings().getRefreshTokenTimeToLive());
        Instant firstIssuedAt = context.getAuthorization() == null ? null
                : AuthorizationStore.firstIssuedAt(context.getAuthorization());
        if (firstIssuedAt == null) {
            throw new IllegalStateException("인가 코드 발급 시각이 없는 인가에는 갱신 토큰을 줄 수 없습니다");
        }
        Instant absoluteLimit = firstIssuedAt.plus(AuthorizationStore.ABSOLUTE_LIFETIME);
        if (!issuedAt.isBefore(absoluteLimit)) {
            // 오류 설명은 ASCII만 쓸 수 있다(RFC 6749 5.2)
            throw new OAuth2AuthenticationException(new OAuth2Error(OAuth2ErrorCodes.INVALID_GRANT,
                    "The authorization is older than 30 days. Sign in again.", null));
        }
        return new OAuth2RefreshToken(REFRESH_TOKENS.generateKey(), issuedAt,
                expiresAt.isAfter(absoluteLimit) ? absoluteLimit : expiresAt);
    }

    /**
     * 공개 클라이언트(client_id만 보냄)의 갱신 요청을 클라이언트 인증으로 바꾼다. Spring 기본은 공개 클라이언트를 PKCE 코드
     * 교환에서만 알아봐, 갱신 요청이 invalid_client로 거절된다. 갱신 토큰 자체의 확인(이 클라이언트에 발급한 토큰인지,
     * 만료나 교체로 무효가 됐는지)은 Spring의 갱신 처리가 한다.
     */
    private static Authentication publicRefreshClient(HttpServletRequest request) {
        if (!AuthorizationGrantType.REFRESH_TOKEN.getValue().equals(request.getParameter(OAuth2ParameterNames.GRANT_TYPE))
                || request.getHeader(HttpHeaders.AUTHORIZATION) != null
                || request.getParameter(OAuth2ParameterNames.CLIENT_SECRET) != null) {
            return null;
        }
        String[] clientIds = request.getParameterValues(OAuth2ParameterNames.CLIENT_ID);
        if (clientIds == null || clientIds.length != 1 || !StringUtils.hasText(clientIds[0])) {
            return null;
        }
        return new OAuth2ClientAuthenticationToken(clientIds[0], ClientAuthenticationMethod.NONE, null,
                Map.of(OAuth2ParameterNames.GRANT_TYPE, AuthorizationGrantType.REFRESH_TOKEN.getValue()));
    }

    private record PublicRefreshClientAuthentication(RegisteredClientRepository clients)
            implements AuthenticationProvider {

        @Override
        public Authentication authenticate(Authentication authentication) {
            OAuth2ClientAuthenticationToken client = (OAuth2ClientAuthenticationToken) authentication;
            if (!ClientAuthenticationMethod.NONE.equals(client.getClientAuthenticationMethod())
                    || !AuthorizationGrantType.REFRESH_TOKEN.getValue()
                    .equals(client.getAdditionalParameters().get(OAuth2ParameterNames.GRANT_TYPE))) {
                return null;
            }
            RegisteredClient registered = clients.findByClientId((String) client.getPrincipal());
            if (registered == null
                    || !registered.getClientAuthenticationMethods().contains(ClientAuthenticationMethod.NONE)) {
                throw new OAuth2AuthenticationException(OAuth2ErrorCodes.INVALID_CLIENT);
            }
            return new OAuth2ClientAuthenticationToken(registered, ClientAuthenticationMethod.NONE, null);
        }

        @Override
        public boolean supports(Class<?> authentication) {
            return OAuth2ClientAuthenticationToken.class.isAssignableFrom(authentication);
        }
    }

    /**
     * 동의 화면의 제출(POST /oauth2/consent)을 받는다. Spring 인가 서버는 동의 제출을 인가 엔드포인트(POST
     * /oauth2/authorize)로 받지만, 계약의 공개 경로는 GET /oauth2/authorize와 GET·POST /oauth2/consent다. 인가
     * 엔드포인트와 같은 필터를 이 경로에 하나 더 두고 동의 제출만 넘긴다. state 확인, 동의 저장, 코드 발급은 인가
     * 엔드포인트와 같은 인증 공급자가 한다. 이 경로는 인가 서버 엔드포인트가 아니라서 CSRF 토큰도 확인한다.
     */
    private static final class ConsentSubmission extends AbstractHttpConfigurer<ConsentSubmission, HttpSecurity> {

        private final AuthorizationRules rules;

        ConsentSubmission(AuthorizationRules rules) {
            this.rules = rules;
        }

        @Override
        public void configure(HttpSecurity http) {
            OAuth2AuthorizationEndpointFilter endpoint = new OAuth2AuthorizationEndpointFilter(
                    http.getSharedObject(AuthenticationManager.class), CONSENT);
            endpoint.setAuthenticationSuccessHandler(rules::sendAuthorizationResponse);
            endpoint.setAuthenticationFailureHandler(rules::sendErrorResponse);
            // 인가 엔드포인트 필터와 같은 클래스라, 이름을 따로 주지 않으면 "이 요청에서 이미 돌았음" 표시가 겹쳐 건너뛴다
            endpoint.setBeanName("consentSubmissionFilter");
            Filter filter = (request, response, chain) -> {
                if (CONSENT_SUBMISSION.matches((HttpServletRequest) request)) {
                    endpoint.doFilter(request, response, chain);
                } else {
                    chain.doFilter(request, response);
                }
            };
            http.addFilterAfter(filter, AuthorizationFilter.class);
        }
    }

    /** 로그인은 위키 사용자 id와 비밀번호다. 비밀번호 해시가 없는 사용자는 없는 사용자와 같게 거절한다. */
    @Bean
    UserDetailsService wikiUsers(UserRepository users) {
        return username -> users.findById(username)
                .filter(user -> user.getPasswordHash() != null)
                .map(user -> org.springframework.security.core.userdetails.User.withUsername(user.getId())
                        .password(user.getPasswordHash())
                        .authorities(List.of())
                        .build())
                .orElseThrow(() -> new UsernameNotFoundException("로그인할 수 없는 사용자입니다"));
    }

    @Bean
    PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    /**
     * Spring Security 방화벽이 거절한 요청(경로 매개변수 ';', 인코딩한 '/', '..' 등)은 토큰이 없는 요청과 같게 본문 없는
     * 401로 답한다. 방화벽이 없던 때에도 이런 경로는 토큰을 요구받았고, 공개 경로도 이런 형태로는 열지 않는다.
     */
    @Bean
    RequestRejectedHandler requestRejectedHandler() {
        return (request, response, e) -> {
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setHeader(HttpHeaders.WWW_AUTHENTICATE, "Bearer");
        };
    }
}
