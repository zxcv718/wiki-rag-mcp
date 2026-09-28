package io.github.zxcv718.wiki.auth;

import java.time.Duration;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.security.oauth2.core.AuthorizationGrantType;
import org.springframework.security.oauth2.core.ClientAuthenticationMethod;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClient;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClientRepository;
import org.springframework.security.oauth2.server.authorization.settings.ClientSettings;
import org.springframework.security.oauth2.server.authorization.settings.TokenSettings;

/**
 * 클라이언트는 두 종류뿐이다(README "클라이언트"). 설정의 사전 등록 클라이언트를 먼저 찾고, 없으면 CIMD 문서로 찾는다.
 * 사전 등록 클라이언트는 공개(none)와 기밀(client_secret_basic)로 나뉘고, CIMD 클라이언트는 모두 공개다. 모두 PKCE가
 * 필수이고 동의를 받는다. 등급은 클라이언트 설정에 적어 두고 토큰을 만들 때 읽는다(AuthorizationRules).
 *
 * 클라이언트를 새로 저장하는 경로는 없다. 동적 등록(DCR)을 열지 않기 때문이다(ADR-24).
 */
class ClientRegistry implements RegisteredClientRepository {

    static final Duration ACCESS_TOKEN_TTL = Duration.ofMinutes(10);
    static final Duration REFRESH_TOKEN_TTL = Duration.ofDays(7);
    private static final String TIER_SETTING = "wiki.client-tier";

    private final Map<String, RegisteredClient> preRegistered;
    private final ClientMetadataDocuments documents;

    ClientRegistry(List<AuthProperties.Client> clients, ClientMetadataDocuments documents) {
        this.preRegistered = clients.stream()
                .map(c -> client(c.id(), c.id(), c.redirectUris(), c.secretHash(), c.tier()))
                .collect(Collectors.toUnmodifiableMap(RegisteredClient::getClientId, Function.identity()));
        this.documents = documents;
    }

    /** CIMD 클라이언트. 공개 클라이언트이고 등급은 항상 외부다. */
    static RegisteredClient publicClient(String clientId, String name, Collection<String> redirectUris) {
        return client(clientId, name, redirectUris, null, ClientTier.EXTERNAL);
    }

    /**
     * secretHash가 있으면 기밀 클라이언트(client_secret_basic), 없으면 공개 클라이언트(none)다. 액세스 토큰 10분, 갱신
     * 토큰 7일이고 갱신 토큰은 쓸 때마다 바꾼다(OAuth 2.1 4.3.1). 동의는 모든 클라이언트가 받고, 기억할지는
     * AuthorizationRules.consentRequired가 정한다.
     */
    private static RegisteredClient client(String clientId, String name, Collection<String> redirectUris,
                                           String secretHash, ClientTier tier) {
        return RegisteredClient.withId(clientId)
                .clientId(clientId)
                .clientName(name)
                .clientSecret(secretHash)
                .clientAuthenticationMethod(secretHash == null
                        ? ClientAuthenticationMethod.NONE : ClientAuthenticationMethod.CLIENT_SECRET_BASIC)
                .authorizationGrantType(AuthorizationGrantType.AUTHORIZATION_CODE)
                .authorizationGrantType(AuthorizationGrantType.REFRESH_TOKEN)
                .redirectUris(uris -> uris.addAll(redirectUris))
                .scope(AuthorizationRules.SCOPE)
                .clientSettings(ClientSettings.builder()
                        .requireProofKey(true)
                        .requireAuthorizationConsent(true)
                        .setting(TIER_SETTING, tier.name())
                        .build())
                .tokenSettings(TokenSettings.builder()
                        .accessTokenTimeToLive(ACCESS_TOKEN_TTL)
                        .refreshTokenTimeToLive(REFRESH_TOKEN_TTL)
                        .reuseRefreshTokens(false)
                        .build())
                .build();
    }

    /** 설정한 등급. 토큰에 넣을 때는 클라이언트가 실제로 비밀로 인증했는지도 함께 본다(AuthorizationRules). */
    static ClientTier tierOf(RegisteredClient client) {
        Object tier = client.getClientSettings().getSetting(TIER_SETTING);
        return ClientTier.INTERNAL.name().equals(tier) ? ClientTier.INTERNAL : ClientTier.EXTERNAL;
    }

    boolean isPreRegistered(String clientId) {
        return clientId != null && preRegistered.containsKey(clientId);
    }

    @Override
    public RegisteredClient findByClientId(String clientId) {
        if (clientId == null) {
            return null;
        }
        RegisteredClient client = preRegistered.get(clientId);
        return client != null ? client : documents.find(clientId);
    }

    /** 클라이언트의 id와 client_id를 같게 두어, id로 찾아도 같은 경로를 탄다. */
    @Override
    public RegisteredClient findById(String id) {
        return findByClientId(id);
    }

    @Override
    public void save(RegisteredClient registeredClient) {
        throw new UnsupportedOperationException("클라이언트는 설정 파일과 CIMD로만 받습니다 (ADR-24)");
    }
}
