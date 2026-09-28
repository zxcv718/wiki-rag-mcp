package io.github.zxcv718.wiki.auth;

import java.util.Locale;

/**
 * 클라이언트 신뢰 등급 (ADR-17, ADR-24). 인가 서버가 정해 액세스 토큰의 client_tier에 넣고, MCP 서버는 그 값만 읽는다.
 * 사내 등급은 비밀로 인증하는 기밀 사전 등록 클라이언트만 받는다.
 */
public enum ClientTier {
    INTERNAL, EXTERNAL;

    /** 토큰에 넣는 값 (internal, external). */
    public String claimValue() {
        return name().toLowerCase(Locale.ROOT);
    }
}
