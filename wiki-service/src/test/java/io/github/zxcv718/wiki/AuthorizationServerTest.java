package io.github.zxcv718.wiki;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.contains;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.httpBasic;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.redirectedUrl;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.jayway.jsonpath.JsonPath;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.crypto.RSASSAVerifier;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import io.github.zxcv718.wiki.auth.AuthorizationStore;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.springframework.util.MultiValueMap;
import org.springframework.web.util.UriComponentsBuilder;
import org.springframework.web.util.UriUtils;

/**
 * 인가 서버의 계약(README "인가 서버")을 브라우저와 클라이언트가 하는 순서대로 확인한다. 로그인, 동의, 코드 교환, 갱신,
 * RFC 8707 대상 지정, PKCE, 공개 경로, CIMD 클라이언트.
 *
 * Spring 인가 서버의 인가·동의 기록은 메모리에 있어 테스트 사이에 남는다. 동의 화면을 확인하는 테스트는 다른 테스트가 쓰지
 * 않는 사용자나 클라이언트를 쓴다.
 */
class AuthorizationServerTest extends IntegrationTest {

    @Autowired
    AuthorizationStore authorizationStore;

    private static final String ISSUER = "http://127.0.0.1:8081";
    private static final String CLIENT = "wiki-rag-dev";
    /** 등록한 주소는 http://127.0.0.1/callback이다. 루프백이라 포트만 다른 주소도 받는다. */
    private static final String REDIRECT = "http://127.0.0.1:43210/callback";
    private static final String PASSWORD = "correct horse battery";
    private static final String STATE = "state-123";
    private static final String CSP = "default-src 'none'; style-src 'unsafe-inline'";

    private final String verifier = randomVerifier();

    @BeforeEach
    void setUp() throws Exception {
        importFixture();
    }

    // ----- 전체 흐름 -----

    @Test
    void fullFlowIssuesVerifiableTokenAndRotatesRefreshToken() throws Exception {
        setPassword("taeyang");
        MockHttpSession session = new MockHttpSession();

        // 로그인하지 않았으면 로그인 화면으로 보낸다
        String login = redirect(mvc.perform(get(authorizeUri(Map.of())).session(session)));
        assertThat(login).endsWith("/login");
        mvc.perform(get("/login").session(session))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("위키 사용자 id")))
                .andExpect(content().string(containsString("name=\"_csrf\"")));

        // 로그인하면 원래 인가 요청으로, 처음이라 동의 화면으로 간다
        String back = redirect(mvc.perform(post("/login").param("username", "taeyang").param("password", PASSWORD)
                .with(csrf()).session(session)));
        String consent = redirect(mvc.perform(get(URI.create(back)).session(session)));
        assertThat(consent).contains("/oauth2/consent?");
        mvc.perform(get(URI.create(consent)).session(session))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("<h1 class=\"host\">wiki-rag-dev</h1>")))
                .andExpect(content().string(containsString("wiki:read")))
                .andExpect(content().string(containsString("taeyang님의 권한으로")))
                .andExpect(content().string(containsString("<dt>허용하면 돌아갈 곳</dt><dd>이 컴퓨터에서 실행 중인 앱 <code>127.0.0.1</code>")))
                .andExpect(content().string(not(containsString("class=\"warning\""))));

        // 동의하면 code, state, iss를 붙여 돌려보낸다(RFC 9207)
        String callback = redirect(approve(session, CLIENT, query(consent).get("state")));
        assertThat(callback).startsWith(REDIRECT + "?");
        Map<String, String> response = query(callback);
        assertThat(response).containsEntry("state", STATE).containsEntry("iss", ISSUER);

        String tokens = mvc.perform(exchange(response.get("code"), Map.of()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token_type").value("Bearer"))
                .andExpect(jsonPath("$.scope").value("wiki:read"))
                .andReturn().getResponse().getContentAsString();

        JWTClaimsSet claims = verifiedClaims(JsonPath.read(tokens, "$.access_token"));
        assertThat(claims.getIssuer()).isEqualTo(ISSUER);
        assertThat(claims.getSubject()).isEqualTo("taeyang");
        assertThat(claims.getAudience()).containsExactly(RESOURCE);
        assertThat(claims.getStringClaim("client_id")).isEqualTo(CLIENT);
        // 공개 클라이언트는 설정과 상관없이 외부 등급이다
        assertThat(claims.getStringClaim("client_tier")).isEqualTo("external");
        assertThat(claims.getStringClaim("scope")).isEqualTo("wiki:read");
        assertThat(claims.getJWTID()).isNotBlank();
        assertThat(Duration.between(claims.getIssueTime().toInstant(), claims.getExpirationTime().toInstant()))
                .isEqualTo(Duration.ofMinutes(10));

        // 갱신 토큰은 쓸 때마다 바뀌고, 쓴 것은 다시 쓸 수 없다
        String firstRefresh = JsonPath.read(tokens, "$.refresh_token");
        String refreshed = mvc.perform(refresh(firstRefresh, Map.of()))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        String secondRefresh = JsonPath.read(refreshed, "$.refresh_token");
        assertThat(secondRefresh).isNotBlank().isNotEqualTo(firstRefresh);
        JWTClaimsSet refreshedClaims = verifiedClaims(JsonPath.read(refreshed, "$.access_token"));
        assertThat(refreshedClaims.getAudience()).containsExactly(RESOURCE);
        assertThat(refreshedClaims.getSubject()).isEqualTo("taeyang");

        assertThat(refreshedClaims.getStringClaim("client_tier")).isEqualTo("external");

        // 이미 바뀐 옛 토큰이 다시 오면 탈취로 보고 인가 전체를 무효로 한다. 살아 있던 새 토큰도 더는 쓸 수 없다
        mvc.perform(refresh(firstRefresh, Map.of()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_grant"));
        mvc.perform(refresh(secondRefresh, Map.of("resource", RESOURCE)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_grant"));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM oauth2_authorization", Long.class)).isZero();
    }

    /** 동의를 거부하면 access_denied를 돌려보낸다. 이때도 iss를 붙인다. */
    @Test
    void deniedConsentRedirectsWithAccessDenied() throws Exception {
        setPassword("minjun");
        MockHttpSession session = login("minjun");

        String consent = redirect(mvc.perform(get(authorizeUri(Map.of())).session(session)));
        String callback = redirect(mvc.perform(post("/oauth2/consent").param("client_id", CLIENT)
                .param("state", query(consent).get("state")).with(csrf()).session(session)));

        assertThat(query(callback)).containsEntry("error", "access_denied").containsEntry("state", STATE)
                .containsEntry("iss", ISSUER);
    }

    // ----- 토큰 대상 지정 (RFC 8707) -----

    @Test
    void authorizationWithoutOrWithUnknownResourceIsInvalidTarget() throws Exception {
        Map<String, String> missing = new HashMap<>();
        missing.put("resource", null);
        for (Map<String, String> override : List.of(missing, Map.of("resource", "http://127.0.0.1:8000/other"),
                Map.of("resource", RESOURCE + "/"))) {
            String callback = redirect(mvc.perform(get(authorizeUri(override))));
            assertThat(query(callback)).as(override.toString()).containsEntry("error", "invalid_target")
                    .containsEntry("iss", ISSUER);
        }
        URI twice = URI.create(authorizeUri(Map.of()) + "&resource=" + UriUtils.encode(OTHER_RESOURCE, StandardCharsets.UTF_8));
        assertThat(query(redirect(mvc.perform(get(twice))))).containsEntry("error", "invalid_target");
    }

    /** 코드 교환의 resource는 인가 때 값과 같아야 한다. 거절된 교환으로는 코드가 소모되지 않는다. */
    @Test
    void codeExchangeMustRepeatTheSameResource() throws Exception {
        setPassword("seoyeon");
        String code = code(login("seoyeon"), authorizeUri(Map.of()));

        Map<String, String> missing = new HashMap<>();
        missing.put("resource", null);
        for (Map<String, String> override : List.of(missing, Map.of("resource", OTHER_RESOURCE),
                Map.of("resource", "http://127.0.0.1:8000/other"))) {
            mvc.perform(exchange(code, override))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.error").value("invalid_target"));
        }
        mvc.perform(exchange(code, Map.of())).andExpect(status().isOk());
    }

    /** 다른 대상으로 인가받으면 토큰의 aud도 그 대상이다. 갱신에서 대상을 바꿀 수는 없다. */
    @Test
    void audienceIsTheAuthorizedResourceAndRefreshCannotChangeIt() throws Exception {
        setPassword("seoyeon");
        String code = code(login("seoyeon"), authorizeUri(Map.of("resource", OTHER_RESOURCE)));

        String tokens = mvc.perform(exchange(code, Map.of("resource", OTHER_RESOURCE)))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertThat(verifiedClaims(JsonPath.read(tokens, "$.access_token")).getAudience())
                .containsExactly(OTHER_RESOURCE);

        String refreshToken = JsonPath.read(tokens, "$.refresh_token");
        mvc.perform(refresh(refreshToken, Map.of("resource", RESOURCE)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_target"));
        // 거절된 갱신으로는 갱신 토큰이 바뀌지 않는다
        mvc.perform(refresh(refreshToken, Map.of())).andExpect(status().isOk());
    }

    // ----- PKCE -----

    @Test
    void pkceIsRequiredAndPlainIsRejected() throws Exception {
        Map<String, String> noChallenge = new HashMap<>();
        noChallenge.put("code_challenge", null);
        noChallenge.put("code_challenge_method", null);
        for (Map<String, String> override : List.of(noChallenge, Map.of("code_challenge_method", "plain"),
                Map.of("code_challenge", verifier, "code_challenge_method", "plain"))) {
            String callback = redirect(mvc.perform(get(authorizeUri(override))));
            assertThat(query(callback)).as(override.toString()).containsEntry("error", "invalid_request")
                    .containsEntry("iss", ISSUER);
        }
    }

    @Test
    void wrongCodeVerifierIsRejected() throws Exception {
        setPassword("seoyeon");
        String code = code(login("seoyeon"), authorizeUri(Map.of()));

        mvc.perform(exchange(code, Map.of("code_verifier", randomVerifier())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_grant"));
    }

    /** 확인하지 못한 redirect_uri로는 오류도 돌려보내지 않는다. */
    @Test
    void unregisteredRedirectUriIsNotRedirected() throws Exception {
        for (String redirectUri : List.of("http://127.0.0.1:43210/other", "https://attacker.example/callback",
                "http://localhost:43210/callback")) {
            mvc.perform(get(authorizeUri(Map.of("redirect_uri", redirectUri))))
                    .andExpect(status().isBadRequest())
                    .andExpect(redirectedUrl(null));
        }
    }

    // ----- 메타데이터와 공개 경로 -----

    @Test
    void metadataAdvertisesTheContract() throws Exception {
        mvc.perform(get("/.well-known/oauth-authorization-server"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.issuer").value(ISSUER))
                .andExpect(jsonPath("$.authorization_endpoint").value(ISSUER + "/oauth2/authorize"))
                .andExpect(jsonPath("$.token_endpoint").value(ISSUER + "/oauth2/token"))
                .andExpect(jsonPath("$.jwks_uri").value(ISSUER + "/oauth2/jwks"))
                .andExpect(jsonPath("$.response_types_supported").value(contains("code")))
                .andExpect(jsonPath("$.grant_types_supported").value(contains("authorization_code", "refresh_token")))
                .andExpect(jsonPath("$.code_challenge_methods_supported").value(contains("S256")))
                .andExpect(jsonPath("$.token_endpoint_auth_methods_supported")
                        .value(contains("none", "client_secret_basic")))
                .andExpect(jsonPath("$.scopes_supported").value(contains("wiki:read")))
                .andExpect(jsonPath("$.client_id_metadata_document_supported").value(true))
                .andExpect(jsonPath("$.authorization_response_iss_parameter_supported").value(true))
                .andExpect(jsonPath("$.registration_endpoint").doesNotExist())
                .andExpect(jsonPath("$.revocation_endpoint").doesNotExist())
                .andExpect(jsonPath("$.introspection_endpoint").doesNotExist());
    }

    @Test
    void jwksHasKeyIdForRs256() throws Exception {
        mvc.perform(get("/oauth2/jwks"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.keys[0].kid").isNotEmpty())
                .andExpect(jsonPath("$.keys[0].kty").value("RSA"))
                .andExpect(jsonPath("$.keys[0].alg").value("RS256"))
                .andExpect(jsonPath("$.keys[0].d").doesNotExist());
    }

    /** 공개 경로에 없는 인가 서버 경로(동적 등록, OIDC, 토큰 폐기·조회, POST 인가 요청)는 토큰 없이 열리지 않는다. */
    @Test
    void endpointsOutsidePublicPathsNeedToken() throws Exception {
        for (MockHttpServletRequestBuilder request : List.of(post("/connect/register"), post("/oauth2/register"),
                get("/.well-known/openid-configuration"), get("/userinfo"), post("/oauth2/revoke"),
                post("/oauth2/introspect"), post("/oauth2/device_authorization"), post("/oauth2/par"),
                post("/oauth2/authorize").param("response_type", "code"), get("/oauth2/token"), put("/login"),
                get("/oauth2/jwks/"), get("/login;x=1"))) {
            mvc.perform(request)
                    .andExpect(status().isUnauthorized())
                    .andExpect(content().string(""));
        }
    }

    // ----- 로그인과 비밀번호 -----

    @Test
    void passwordApiValidatesAndHashes() throws Exception {
        asAdmin(put("/admin/users/seoyeon/password").contentType(MediaType.APPLICATION_JSON)
                .content("{\"password\": \"" + PASSWORD + "\"}"))
                .andExpect(status().isNoContent())
                .andExpect(content().string(""));
        String hash = jdbc.queryForObject("SELECT password_hash FROM users WHERE id = 'seoyeon'", String.class);
        assertThat(hash).startsWith("$2").doesNotContain(PASSWORD);

        for (String body : List.of("{\"password\": \"short-11ch\"}", "{}", "{\"password\": null}",
                "{\"password\": \"" + "가".repeat(25) + "\"}", "{\"password\": \"" + PASSWORD + "\", \"x\": 1}")) {
            asAdmin(put("/admin/users/seoyeon/password").contentType(MediaType.APPLICATION_JSON).content(body))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.error").value("bad_request"));
        }
        asAdmin(put("/admin/users/nobody/password").contentType(MediaType.APPLICATION_JSON)
                .content("{\"password\": \"" + PASSWORD + "\"}"))
                .andExpect(status().isNotFound());
        asService(put("/admin/users/seoyeon/password").contentType(MediaType.APPLICATION_JSON)
                .content("{\"password\": \"" + PASSWORD + "\"}"))
                .andExpect(status().isUnauthorized());
        assertThat(outboxRows()).hasSize(FIXTURE_DOCUMENTS);
    }

    @Test
    void userWithoutPasswordHashCannotLogIn() throws Exception {
        mvc.perform(post("/login").param("username", "gaeun").param("password", "").with(csrf()))
                .andExpect(redirectedUrl("/login?error"));
        mvc.perform(post("/login").param("username", "gaeun").param("password", PASSWORD).with(csrf()))
                .andExpect(redirectedUrl("/login?error"));

        setPassword("gaeun");
        mvc.perform(post("/login").param("username", "gaeun").param("password", "wrong password!").with(csrf()))
                .andExpect(redirectedUrl("/login?error"));
        mvc.perform(post("/login").param("username", "gaeun").param("password", PASSWORD).with(csrf()))
                .andExpect(redirectedUrl("/"));
    }

    @Test
    void loginAndConsentPostsNeedCsrfToken() throws Exception {
        setPassword("gaeun");
        mvc.perform(post("/login").param("username", "gaeun").param("password", PASSWORD))
                .andExpect(status().isForbidden());
        mvc.perform(post("/oauth2/consent").param("client_id", CLIENT).param("state", "x").session(login("gaeun")))
                .andExpect(status().isForbidden());
    }

    // ----- CIMD 클라이언트 -----

    /**
     * client_id가 https 주소인 클라이언트. 동의 화면은 주소의 호스트를 크게, 문서가 주장하는 이름은 작게(이스케이프해서)
     * 보인다. 토큰의 client_id는 문서 주소다. 루프백 redirect_uri는 포트만 달라도 받는다.
     */
    @Test
    void cimdClientCompletesFlowWithHostShownOnConsent() throws Exception {
        String clientId = "https://agent.example/oauth/client-metadata.json";
        FakeClientMetadata.serve(clientId, """
                {"client_id": "%s", "client_name": "<b>공식 위키 앱</b>",
                 "redirect_uris": ["http://localhost/callback"], "token_endpoint_auth_method": "none"}
                """.formatted(clientId));
        String redirectUri = "http://localhost:53682/callback";
        setPassword("seoyeon");
        MockHttpSession session = login("seoyeon");

        String consent = redirect(mvc.perform(get(authorizeUri(Map.of("client_id", clientId,
                "redirect_uri", redirectUri))).session(session)));
        mvc.perform(get(URI.create(consent)).session(session))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Security-Policy", CSP))
                .andExpect(header().string("X-Frame-Options", "DENY"))
                .andExpect(content().string(containsString("<h1 class=\"host\">agent.example</h1>")))
                .andExpect(content().string(containsString("&lt;b&gt;공식 위키 앱&lt;/b&gt;")))
                .andExpect(content().string(not(containsString("<b>공식"))))
                .andExpect(content().string(containsString("<dt>앱 주소</dt><dd><code>" + clientId + "</code>")))
                .andExpect(content().string(containsString(
                        "<dt>허용하면 돌아갈 곳</dt><dd>이 컴퓨터에서 실행 중인 앱 <code>localhost</code>")))
                .andExpect(content().string(not(containsString("class=\"warning\""))));

        String callback = redirect(approve(session, clientId, query(consent).get("state")));
        assertThat(callback).startsWith(redirectUri + "?");
        String tokens = mvc.perform(exchange(query(callback).get("code"),
                        Map.of("client_id", clientId, "redirect_uri", redirectUri)))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        JWTClaimsSet claims = verifiedClaims(JsonPath.read(tokens, "$.access_token"));
        assertThat(claims.getStringClaim("client_id")).isEqualTo(clientId);
        assertThat(claims.getStringClaim("client_tier")).isEqualTo("external");

        mvc.perform(refresh(JsonPath.read(tokens, "$.refresh_token"), Map.of("client_id", clientId)))
                .andExpect(status().isOk());
    }

    @Test
    void cimdRedirectUriMustMatchTheDocument() throws Exception {
        String clientId = "https://agent.example/oauth/strict.json";
        FakeClientMetadata.serve(clientId, """
                {"client_id": "%s", "redirect_uris": ["https://agent.example/callback"]}
                """.formatted(clientId));

        for (String redirectUri : List.of("https://agent.example/callback/", "https://agent.example:8443/callback",
                "https://agent.example/other")) {
            mvc.perform(get(authorizeUri(Map.of("client_id", clientId, "redirect_uri", redirectUri))))
                    .andExpect(status().isBadRequest())
                    .andExpect(redirectedUrl(null));
        }
        // 정확히 같은 주소면 인가 요청이 통과해 로그인 화면으로 간다
        assertThat(redirect(mvc.perform(get(authorizeUri(Map.of("client_id", clientId,
                "redirect_uri", "https://agent.example/callback")))))).endsWith("/login");
    }

    /** 가져오지 못한 CIMD 문서(여기서는 문서가 없음)는 모르는 클라이언트로 거절하고 돌려보내지 않는다. */
    @Test
    void unreachableCimdDocumentIsRejected() throws Exception {
        String clientId = "https://agent.example/oauth/missing.json";

        mvc.perform(get(authorizeUri(Map.of("client_id", clientId, "redirect_uri", "http://localhost/callback"))))
                .andExpect(status().isBadRequest())
                .andExpect(redirectedUrl(null));
        mvc.perform(post("/oauth2/token").param("grant_type", "refresh_token").param("refresh_token", "x")
                        .param("client_id", clientId))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("invalid_client"));
    }

    @Test
    void metadataEndpointsAreOpenWithoutToken() throws Exception {
        mvc.perform(get("/.well-known/oauth-authorization-server")).andExpect(status().isOk());
        mvc.perform(get("/oauth2/jwks")).andExpect(status().isOk());
        mvc.perform(get("/login")).andExpect(status().isOk());
        // 내부 API는 여전히 서비스 토큰이 필요하다
        mvc.perform(get("/internal/spaces")).andExpect(status().isUnauthorized());
    }

    @Test
    void consentPageNeedsLogin() throws Exception {
        String login = redirect(mvc.perform(get("/oauth2/consent").param("client_id", CLIENT)
                .param("scope", "wiki:read").param("state", "x")));
        assertThat(login).endsWith("/login");
    }

    // ----- 동의 기억 -----

    /**
     * 공개 클라이언트(CIMD 포함)는 이미 허용했어도 새 인가마다 동의 화면을 보인다(RFC 8252 8.6). 같은 id를 쓴 다른
     * 프로그램이 살아 있는 로그인 세션으로 화면 없이 코드를 받지 못하게 하기 위해서다. 기밀 클라이언트는 비밀로 신원이
     * 확인되므로 이미 허용한 범위면 건너뛴다.
     */
    @Test
    void onlyConfidentialClientsSkipConsentOnceApproved() throws Exception {
        setPassword("gaeun");
        MockHttpSession session = login("gaeun");
        for (int round = 0; round < 2; round++) {
            String consent = redirect(mvc.perform(get(authorizeUri(Map.of())).session(session)));
            assertThat(consent).contains("/oauth2/consent?");
            assertThat(redirect(approve(session, CLIENT, query(consent).get("state")))).startsWith(REDIRECT + "?");
        }

        URI demo = authorizeUri(Map.of("client_id", DEMO_CLIENT, "redirect_uri", DEMO_REDIRECT));
        assertThat(code(session, demo)).isNotBlank();
        assertThat(redirect(mvc.perform(get(demo).session(session)))).startsWith(DEMO_REDIRECT + "?");
    }

    // ----- 클라이언트 등급 -----

    /** 기밀 클라이언트는 비밀로 인증해야 하고, 그때만 설정한 사내 등급을 받는다. */
    @Test
    void confidentialClientMustAuthenticateWithSecretAndGetsInternalTier() throws Exception {
        setPassword("seoyeon");
        String code = code(login("seoyeon"), authorizeUri(Map.of("client_id", DEMO_CLIENT,
                "redirect_uri", DEMO_REDIRECT)));
        Map<String, String> demo = override("client_id", null, "redirect_uri", DEMO_REDIRECT);

        // client_id만 보내 공개 클라이언트처럼 교환하거나, 비밀이 틀리면 거절한다. 거절된 교환으로는 코드가 소모되지 않는다
        mvc.perform(exchange(code, override("client_id", DEMO_CLIENT, "redirect_uri", DEMO_REDIRECT)))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("invalid_client"));
        mvc.perform(exchange(code, demo).with(httpBasic(DEMO_CLIENT, "wrong-secret")))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("invalid_client"));

        String tokens = mvc.perform(exchange(code, demo).with(httpBasic(DEMO_CLIENT, DEMO_SECRET)))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        JWTClaimsSet claims = verifiedClaims(JsonPath.read(tokens, "$.access_token"));
        assertThat(claims.getStringClaim("client_id")).isEqualTo(DEMO_CLIENT);
        assertThat(claims.getStringClaim("client_tier")).isEqualTo("internal");

        String refreshToken = JsonPath.read(tokens, "$.refresh_token");
        mvc.perform(refresh(refreshToken, override("client_id", DEMO_CLIENT)))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("invalid_client"));
        String refreshed = mvc.perform(refresh(refreshToken, override("client_id", null))
                        .with(httpBasic(DEMO_CLIENT, DEMO_SECRET)))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertThat(verifiedClaims(JsonPath.read(refreshed, "$.access_token")).getStringClaim("client_tier"))
                .isEqualTo("internal");
    }

    // ----- 로그인 전 CIMD 오류 -----

    /**
     * 로그인 전 CIMD 클라이언트의 인가 오류는 문서의 redirect_uri로 돌려보내지 않는다. 돌려보내면 누구나 이 인가 서버를
     * 거쳐 아무 주소로 보내는 링크를 만들 수 있다. 로그인한 뒤의 오류는 돌려보낸다.
     */
    @Test
    void cimdErrorsBeforeLoginAreNotRedirected() throws Exception {
        String clientId = "https://agent.example/oauth/open-redirect.json";
        String landing = "https://phish.example/landing";
        FakeClientMetadata.serve(clientId, """
                {"client_id": "%s", "redirect_uris": ["%s"]}
                """.formatted(clientId, landing));
        for (Map<String, String> broken : List.of(override("resource", null), override("code_challenge", null),
                override("code_challenge_method", "plain"), override("scope", "admin"))) {
            Map<String, String> parameters = new HashMap<>(broken);
            parameters.put("client_id", clientId);
            parameters.put("redirect_uri", landing);
            mvc.perform(get(authorizeUri(parameters)))
                    .andExpect(status().isBadRequest())
                    .andExpect(redirectedUrl(null));
        }

        setPassword("seoyeon");
        Map<String, String> parameters = override("client_id", clientId, "redirect_uri", landing, "resource", null);
        String callback = redirect(mvc.perform(get(authorizeUri(parameters)).session(login("seoyeon"))));
        assertThat(callback).startsWith(landing + "?");
        assertThat(query(callback)).containsEntry("error", "invalid_target");
    }

    // ----- 갱신 토큰의 수명과 대상 -----

    /** 갱신을 이어 가도 처음 로그인에서 30일이 지나면 더 발급하지 않는다. 새 갱신 토큰의 만료도 그 시각을 넘지 않는다. */
    @Test
    void refreshStopsThirtyDaysAfterFirstLogin() throws Exception {
        String refreshToken = JsonPath.read(tokensFor("seoyeon"), "$.refresh_token");

        jdbc.update("UPDATE oauth2_authorization SET authorization_code_issued_at = now() - interval '29 days 23 hours'");
        String refreshed = mvc.perform(refresh(refreshToken, Map.of()))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        Instant expiresAt = jdbc.queryForObject("SELECT refresh_token_expires_at FROM oauth2_authorization",
                Timestamp.class).toInstant();
        assertThat(expiresAt).isBefore(Instant.now().plus(Duration.ofHours(2)));

        jdbc.update("UPDATE oauth2_authorization SET authorization_code_issued_at = now() - interval '31 days'");
        mvc.perform(refresh(JsonPath.read(refreshed, "$.refresh_token"), Map.of()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_grant"));
    }

    /** 설정에서 뺀 리소스로 받은 인가는 갱신할 수 없다. 여기서는 저장된 인가의 resource를 설정에 없는 값으로 바꿔 흉내 낸다. */
    @Test
    void refreshRechecksThatTheResourceIsStillConfigured() throws Exception {
        String refreshToken = JsonPath.read(tokensFor("seoyeon"), "$.refresh_token");
        assertThat(jdbc.update("UPDATE oauth2_authorization SET attributes = replace(attributes, ?, ?)",
                RESOURCE, "http://127.0.0.1:7000/removed")).isEqualTo(1);

        mvc.perform(refresh(refreshToken, Map.of()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_target"));
    }

    // ----- 남용 방지 -----

    /** 한도는 1분 동안 남아 다른 테스트에 번지지 않게, 이 테스트만 쓰는 사용자로 확인한다. */
    @Test
    void loginFailuresAreLimitedPerIpAndPerUser() throws Exception {
        asAdmin(put("/admin/users/limited").contentType(MediaType.APPLICATION_JSON).content("{\"name\": \"한도 확인\"}"))
                .andExpect(status().isOk());
        setPassword("limited");
        // 한 IP에서 여러 사용자 id로 10번 틀리면, 그 IP에서는 맞는 비밀번호로도 1분 동안 로그인할 수 없다
        for (int i = 0; i < 10; i++) {
            mvc.perform(login("198.51.100.10", "nobody" + i, "wrong password!"))
                    .andExpect(redirectedUrl("/login?error"));
        }
        mvc.perform(login("198.51.100.10", "limited", PASSWORD))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().string("Retry-After", "60"));
        mvc.perform(login("198.51.100.11", "limited", PASSWORD)).andExpect(redirectedUrl("/"));

        // IP를 바꿔 가며 한 사용자 id로 10번 틀려도 그 id는 막힌다
        for (int i = 0; i < 10; i++) {
            mvc.perform(login("198.51.100." + (20 + i), "limited", "wrong password!"))
                    .andExpect(redirectedUrl("/login?error"));
        }
        mvc.perform(login("198.51.100.99", "limited", PASSWORD)).andExpect(status().isTooManyRequests());
    }

    @Test
    void authorizationRequestsAreLimitedPerIp() throws Exception {
        for (int i = 0; i < 60; i++) {
            mvc.perform(get(authorizeUri(Map.of())).with(from("198.51.100.200")))
                    .andExpect(status().is3xxRedirection());
        }
        mvc.perform(get(authorizeUri(Map.of())).with(from("198.51.100.200")))
                .andExpect(status().isTooManyRequests());
        mvc.perform(get(authorizeUri(Map.of())).with(from("198.51.100.201")))
                .andExpect(status().is3xxRedirection());
    }

    @Test
    void loginPageHasCspAndFrameProtection() throws Exception {
        mvc.perform(get("/login"))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Security-Policy", CSP))
                .andExpect(header().string("X-Frame-Options", "DENY"));
    }

    // ----- 동의 화면 -----

    /** client_id와 돌아갈 곳의 호스트가 같으면 경고하지 않는다. */
    @Test
    void consentDoesNotWarnWhenHostsMatch() throws Exception {
        String clientId = "https://tools.example/oauth/client.json";
        FakeClientMetadata.serve(clientId, """
                {"client_id": "%s", "redirect_uris": ["https://tools.example/callback"]}
                """.formatted(clientId));
        setPassword("seoyeon");
        MockHttpSession session = login("seoyeon");

        String consent = redirect(mvc.perform(get(authorizeUri(Map.of("client_id", clientId,
                "redirect_uri", "https://tools.example/callback"))).session(session)));
        mvc.perform(get(URI.create(consent)).session(session))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("<dt>허용하면 돌아갈 곳</dt><dd><code>tools.example</code>")))
                .andExpect(content().string(not(containsString("class=\"warning\""))));
    }

    /** 돌아갈 곳이 앱 주소와 다른 호스트의 https 주소면 경고한다. 공용 저장소에 문서를 올려 믿을 만해 보이게 하는 경우다. */
    @Test
    void consentWarnsWhenRedirectHostDiffers() throws Exception {
        String clientId = "https://storage.example/bucket/client.json";
        FakeClientMetadata.serve(clientId, """
                {"client_id": "%s", "redirect_uris": ["https://collector.example/callback"]}
                """.formatted(clientId));
        setPassword("seoyeon");
        MockHttpSession session = login("seoyeon");

        String consent = redirect(mvc.perform(get(authorizeUri(Map.of("client_id", clientId,
                "redirect_uri", "https://collector.example/callback"))).session(session)));
        mvc.perform(get(URI.create(consent)).session(session))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("<dt>허용하면 돌아갈 곳</dt><dd><code>collector.example</code>")))
                .andExpect(content().string(containsString(
                        "앱 주소의 호스트(storage.example)와 돌아갈 곳의 호스트(collector.example)가 다릅니다")));
    }

    /** 돌아갈 곳이 루프백이면 앱 주소의 호스트와 달라도 경고하지 않는다. Claude Code 같은 네이티브 앱의 경우다. */
    @Test
    void consentDoesNotWarnForLoopbackRedirect() throws Exception {
        String clientId = "https://native.example/oauth/client-metadata";
        FakeClientMetadata.serve(clientId, """
                {"client_id": "%s", "redirect_uris": ["http://localhost/callback"]}
                """.formatted(clientId));
        setPassword("seoyeon");
        MockHttpSession session = login("seoyeon");

        String consent = redirect(mvc.perform(get(authorizeUri(Map.of("client_id", clientId,
                "redirect_uri", "http://localhost:43127/callback"))).session(session)));
        mvc.perform(get(URI.create(consent)).session(session))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString(
                        "<dt>허용하면 돌아갈 곳</dt><dd>이 컴퓨터에서 실행 중인 앱 <code>localhost</code>")))
                .andExpect(content().string(not(containsString("class=\"warning\""))));
    }

    /** 동의 화면의 내용은 state로 찾은 진행 중인 인가에서 읽는다. 다른 사용자의 state로는 열리지 않는다. */
    @Test
    void consentPageRejectsAnotherUsersState() throws Exception {
        setPassword("seoyeon");
        setPassword("minjun");
        String consent = redirect(mvc.perform(get(authorizeUri(Map.of())).session(login("seoyeon"))));

        mvc.perform(get(URI.create(consent)).session(login("minjun")))
                .andExpect(status().isBadRequest())
                .andExpect(content().string(containsString("알 수 없는 요청입니다")));
    }

    // ----- CIMD 가져오기 -----

    /** 한 요청에서 인증 공급자 여러 개가 같은 클라이언트를 찾아도 문서는 한 번만 가져온다. */
    @Test
    void rejectedTokenRequestFetchesTheDocumentOnce() throws Exception {
        String clientId = "https://agent.example/oauth/fetched-once.json";

        mvc.perform(post("/oauth2/token").param("grant_type", "refresh_token").param("refresh_token", "x")
                        .param("client_id", clientId))
                .andExpect(status().isUnauthorized());
        assertThat(FakeClientMetadata.FETCHES.get(clientId)).hasValue(1);
    }

    // ----- 저장소 -----

    @Test
    void expiredRecordsAreRemoved() throws Exception {
        tokensFor("seoyeon");
        setPassword("minjun");
        redirect(mvc.perform(get(authorizeUri(Map.of())).session(login("minjun"))));
        jdbc.update("INSERT INTO oauth2_rotated_refresh_token VALUES (?, 'old', now() - interval '31 days'),"
                + " (?, 'recent', now() - interval '1 day')", "a".repeat(64), "b".repeat(64));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM oauth2_authorization", Long.class)).isEqualTo(2);

        authorizationStore.removeExpired();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM oauth2_authorization", Long.class)).isEqualTo(2);

        // 발급한 토큰이 모두 만료된 인가와, 한 시간 넘게 동의를 기다린 인가를 지운다
        jdbc.update("UPDATE oauth2_authorization SET authorization_code_expires_at = now() - interval '1 day',"
                + " access_token_expires_at = now() - interval '1 day', refresh_token_expires_at = now() - interval '1 minute'"
                + " WHERE refresh_token_value IS NOT NULL");
        jdbc.update("UPDATE oauth2_authorization SET created_at = now() - interval '2 hours' WHERE state IS NOT NULL");
        authorizationStore.removeExpired();

        assertThat(jdbc.queryForObject("SELECT count(*) FROM oauth2_authorization", Long.class)).isZero();
        assertThat(jdbc.queryForList("SELECT authorization_id FROM oauth2_rotated_refresh_token", String.class))
                .containsExactly("recent");
    }

    // ----- 도우미 -----

    /** 로그인, 동의, 코드 교환까지 마친 토큰 응답. */
    private String tokensFor(String userId) throws Exception {
        setPassword(userId);
        String code = code(login(userId), authorizeUri(Map.of()));
        return mvc.perform(exchange(code, Map.of())).andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
    }

    private static MockHttpServletRequestBuilder login(String ip, String userId, String password) {
        return post("/login").param("username", userId).param("password", password).with(csrf()).with(from(ip));
    }

    private static RequestPostProcessor from(String ip) {
        return request -> {
            request.setRemoteAddr(ip);
            return request;
        };
    }

    /** 이름과 값을 번갈아 받는다. 값이 null이면 그 매개변수를 뺀다. */
    private static Map<String, String> override(String... namesAndValues) {
        Map<String, String> parameters = new HashMap<>();
        for (int i = 0; i < namesAndValues.length; i += 2) {
            parameters.put(namesAndValues[i], namesAndValues[i + 1]);
        }
        return parameters;
    }

    private void setPassword(String userId) throws Exception {
        asAdmin(put("/admin/users/" + userId + "/password").contentType(MediaType.APPLICATION_JSON)
                .content("{\"password\": \"" + PASSWORD + "\"}"))
                .andExpect(status().isNoContent());
    }

    private MockHttpSession login(String userId) throws Exception {
        MockHttpSession session = new MockHttpSession();
        mvc.perform(post("/login").param("username", userId).param("password", PASSWORD).with(csrf()).session(session))
                .andExpect(redirectedUrl("/"));
        return session;
    }

    /** 로그인한 세션으로 인가를 받아 코드를 돌려준다. 이미 동의했으면 동의 화면을 건너뛴다. */
    private String code(MockHttpSession session, URI authorize) throws Exception {
        String next = redirect(mvc.perform(get(authorize).session(session)));
        if (next.contains("/oauth2/consent?")) {
            Map<String, String> consent = query(next);
            next = redirect(approve(session, consent.get("client_id"), consent.get("state")));
        }
        return query(next).get("code");
    }

    private ResultActions approve(MockHttpSession session, String clientId, String state) throws Exception {
        return mvc.perform(post("/oauth2/consent").param("client_id", clientId).param("state", state)
                .param("scope", "wiki:read").with(csrf()).session(session));
    }

    /** 기본값으로 만든 인가 요청. override의 값이 null이면 그 매개변수를 뺀다. */
    private URI authorizeUri(Map<String, String> override) {
        Map<String, String> parameters = new HashMap<>(Map.of(
                "response_type", "code", "client_id", CLIENT, "redirect_uri", REDIRECT, "scope", "wiki:read",
                "state", STATE, "code_challenge", challenge(verifier), "code_challenge_method", "S256",
                "resource", RESOURCE));
        parameters.putAll(override);
        UriComponentsBuilder uri = UriComponentsBuilder.fromPath("/oauth2/authorize");
        parameters.forEach((name, value) -> {
            if (value != null) {
                uri.queryParam(name, UriUtils.encode(value, StandardCharsets.UTF_8));
            }
        });
        return URI.create(uri.build(true).toUriString());
    }

    private MockHttpServletRequestBuilder exchange(String code, Map<String, String> override) {
        Map<String, String> parameters = new HashMap<>(Map.of("grant_type", "authorization_code", "code", code,
                "redirect_uri", REDIRECT, "client_id", CLIENT, "code_verifier", verifier, "resource", RESOURCE));
        parameters.putAll(override);
        return form(parameters);
    }

    private MockHttpServletRequestBuilder refresh(String refreshToken, Map<String, String> override) {
        Map<String, String> parameters = new HashMap<>(Map.of("grant_type", "refresh_token",
                "refresh_token", refreshToken, "client_id", CLIENT));
        parameters.putAll(override);
        return form(parameters);
    }

    private static MockHttpServletRequestBuilder form(Map<String, String> parameters) {
        MockHttpServletRequestBuilder request = post("/oauth2/token").contentType(MediaType.APPLICATION_FORM_URLENCODED);
        parameters.forEach((name, value) -> {
            if (value != null) {
                request.param(name, value);
            }
        });
        return request;
    }

    private JWTClaimsSet verifiedClaims(String accessToken) throws Exception {
        JWKSet keys = JWKSet.parse(mvc.perform(get("/oauth2/jwks")).andReturn().getResponse().getContentAsString());
        SignedJWT jwt = SignedJWT.parse(accessToken);
        assertThat(jwt.getHeader().getAlgorithm()).isEqualTo(JWSAlgorithm.RS256);
        RSAKey key = (RSAKey) keys.getKeyByKeyId(jwt.getHeader().getKeyID());
        assertThat(key).isNotNull();
        assertThat(jwt.verify(new RSASSAVerifier(key))).isTrue();
        return jwt.getJWTClaimsSet();
    }

    private static String redirect(ResultActions result) throws Exception {
        String location = result.andExpect(status().is3xxRedirection()).andReturn().getResponse().getRedirectedUrl();
        assertThat(location).isNotNull();
        return location;
    }

    private static Map<String, String> query(String url) {
        MultiValueMap<String, String> parameters = UriComponentsBuilder.fromUriString(url).build().getQueryParams();
        Map<String, String> decoded = new HashMap<>();
        parameters.forEach((name, values) -> decoded.put(name,
                values.getFirst() == null ? "" : UriUtils.decode(values.getFirst(), StandardCharsets.UTF_8)));
        return decoded;
    }

    private static String randomVerifier() {
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private static String challenge(String verifier) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(verifier.getBytes(StandardCharsets.US_ASCII));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(digest);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
