package io.github.zxcv718.wiki.domain;

import java.util.Locale;

/** 문서 등급 (ADR-17). 선언 순서가 등급의 높낮이다. */
public enum Classification {
    GENERAL,
    CONFIDENTIAL;

    /** API와 이벤트에 쓰는 이름 (general, confidential). */
    public String value() {
        return name().toLowerCase(Locale.ROOT);
    }

    public static Classification fromValue(String value) {
        return valueOf(value.toUpperCase(Locale.ROOT));
    }

    /**
     * 스페이스 기본 등급과 문서 등급 중 높은 쪽. 문서 등급은 기본값보다 높일 때만 의미가 있어서, 기밀 스페이스의
     * 문서를 일반으로 적어도 기밀로 남는다.
     */
    public static Classification effective(Classification spaceDefault, Classification document) {
        if (document == null) {
            return spaceDefault;
        }
        return document.compareTo(spaceDefault) > 0 ? document : spaceDefault;
    }
}
