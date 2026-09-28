package io.github.zxcv718.wiki.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Timestamp;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.security.oauth2.core.OAuth2RefreshToken;
import org.springframework.security.oauth2.server.authorization.OAuth2Authorization;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationCode;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationService;
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType;

/**
 * 인가 기록 저장소. Spring이 주는 JDBC 저장소(JdbcOAuth2AuthorizationService, 표는 V3)에 두 가지를 더한다.
 *
 * - 갱신 토큰 재사용 탐지(OAuth 2.1 4.3.1): 갱신으로 바뀐 옛 토큰의 해시를 기억하고, 그 토큰이 다시 들어오면 탈취로 보고
 *   그 인가 전체를 지운다. 공격자와 사용자 중 누가 먼저 갱신했든 다음 갱신에서 들키고, 살아 있는 갱신 토큰도 함께 무효가
 *   된다. Spring 기본은 옛 토큰을 "모르는 토큰"으로만 거절한다.
 * - 만료된 기록 정리: 한 시간마다 만료된 인가, 오래 동의를 기다린 인가, 30일이 지난 옛 토큰 해시를 지운다.
 */
public class AuthorizationStore implements OAuth2AuthorizationService {

    /** 동의 화면에서 이만큼 지나도록 답이 없으면 버린다. */
    private static final Duration PENDING_TTL = Duration.ofHours(1);

    /** 인가는 처음 로그인에서 30일 뒤면 더 갱신할 수 없으므로(AuthorizationServerConfig), 옛 토큰도 그만큼만 기억한다. */
    static final Duration ABSOLUTE_LIFETIME = Duration.ofDays(30);

    private static final Logger log = LoggerFactory.getLogger(AuthorizationStore.class);

    private final OAuth2AuthorizationService delegate;
    private final JdbcTemplate jdbc;
    private final Clock clock;

    AuthorizationStore(OAuth2AuthorizationService delegate, JdbcTemplate jdbc, Clock clock) {
        this.delegate = delegate;
        this.jdbc = jdbc;
        this.clock = clock;
    }

    /**
     * 갱신 토큰이 바뀌었으면 옛 토큰의 해시를 남긴다. 같은 토큰으로 두 갱신이 동시에 들어오면 늦게 저장한 쪽이 앞의 새 토큰을
     * 옛 토큰으로 남기므로, 그 토큰을 받은 쪽이 다음에 갱신할 때 인가 전체가 지워진다. 재사용과 구별할 수 없어 안전한 쪽을 택했다.
     */
    @Override
    public void save(OAuth2Authorization authorization) {
        OAuth2Authorization previous = delegate.findById(authorization.getId());
        delegate.save(authorization);
        String replaced = refreshTokenValue(previous);
        if (replaced != null && !replaced.equals(refreshTokenValue(authorization))) {
            jdbc.update("INSERT INTO oauth2_rotated_refresh_token (token_hash, authorization_id, rotated_at)"
                    + " VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    sha256(replaced), authorization.getId(), Timestamp.from(clock.instant()));
        }
    }

    @Override
    public void remove(OAuth2Authorization authorization) {
        delegate.remove(authorization);
    }

    @Override
    public OAuth2Authorization findById(String id) {
        return delegate.findById(id);
    }

    /**
     * 갱신 토큰으로 찾지 못했는데 예전에 바뀐 토큰이면, 그 인가를 지우고 없다고 답한다. 호출한 쪽(Spring의 갱신 처리)은
     * invalid_grant로 거절한다. 인가 행을 읽지 않고 바로 지워, 지우는 길에 CIMD 문서를 다시 가져오지 않는다.
     */
    @Override
    public OAuth2Authorization findByToken(String token, OAuth2TokenType tokenType) {
        OAuth2Authorization found = delegate.findByToken(token, tokenType);
        if (found != null || !(tokenType == null || OAuth2TokenType.REFRESH_TOKEN.equals(tokenType))) {
            return found;
        }
        List<String> reused = jdbc.queryForList(
                "SELECT authorization_id FROM oauth2_rotated_refresh_token WHERE token_hash = ?", String.class,
                sha256(token));
        for (String authorizationId : reused) {
            int removed = jdbc.update("DELETE FROM oauth2_authorization WHERE id = ?", authorizationId);
            if (removed > 0) {
                log.warn("이미 바뀐 갱신 토큰이 다시 들어와 인가 {}를 무효로 했습니다", authorizationId);
            }
        }
        return null;
    }

    /** 만료된 기록을 지운다. 첫 실행도 한 시간 뒤라 테스트 중에는 돌지 않는다(테스트는 직접 부른다). */
    @Scheduled(fixedDelayString = "PT1H", initialDelayString = "PT1H")
    public void removeExpired() {
        Timestamp now = Timestamp.from(clock.instant());
        // GREATEST는 NULL을 건너뛴다. 코드, 액세스 토큰, 갱신 토큰 중 가장 늦은 만료가 지났으면 쓸 수 있는 것이 없다
        int expired = jdbc.update("DELETE FROM oauth2_authorization WHERE GREATEST(authorization_code_expires_at,"
                + " access_token_expires_at, refresh_token_expires_at) < ?", now);
        int abandoned = jdbc.update("DELETE FROM oauth2_authorization WHERE authorization_code_value IS NULL"
                + " AND access_token_value IS NULL AND refresh_token_value IS NULL AND created_at < ?",
                Timestamp.from(clock.instant().minus(PENDING_TTL)));
        int rotated = jdbc.update("DELETE FROM oauth2_rotated_refresh_token WHERE rotated_at < ?",
                Timestamp.from(clock.instant().minus(ABSOLUTE_LIFETIME)));
        if (expired + abandoned + rotated > 0) {
            log.info("만료된 인가 기록을 지웠습니다: 만료 {}건, 동의 대기 {}건, 옛 갱신 토큰 {}건", expired, abandoned, rotated);
        }
    }

    private static String refreshTokenValue(OAuth2Authorization authorization) {
        if (authorization == null) {
            return null;
        }
        OAuth2Authorization.Token<OAuth2RefreshToken> token = authorization.getRefreshToken();
        return token == null ? null : token.getToken().getTokenValue();
    }

    static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** 처음 로그인(인가 코드 발급) 시각. 갱신을 이어 가도 여기서 30일이 지나면 더 갱신하지 않는다. */
    static Instant firstIssuedAt(OAuth2Authorization authorization) {
        OAuth2Authorization.Token<OAuth2AuthorizationCode> code = authorization.getToken(OAuth2AuthorizationCode.class);
        return code == null ? null : code.getToken().getIssuedAt();
    }
}
