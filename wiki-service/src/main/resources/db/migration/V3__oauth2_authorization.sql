-- 인가 서버의 인가 기록과 동의 (ADR-24). 위키를 다시 시작해도 갱신 토큰과 동의가 남게 DB에 둔다.
-- 기존 표는 건드리지 않고 새 표만 만든다.
--
-- Spring 인가 서버가 주는 스키마(oauth2-authorization-schema.sql, oauth2-authorization-consent-schema.sql)를
-- 스키마 파일의 안내대로 PostgreSQL에 맞췄다. blob은 text, timestamp는 timestamptz다. 그 밖에 바꾼 것은 아래와 같다.
-- - registered_client_id는 text다. CIMD 클라이언트는 id가 메타데이터 문서 주소(최대 2048자)라 varchar(100)에 들어가지 않는다.
-- - created_at을 더했다. 동의를 기다리는 기록은 만료 시각이 없어, 오래된 것을 지울 기준으로 쓴다(AuthorizationStore).
-- - 토큰으로 찾는 열에 색인을 걸었다. 액세스 토큰(JWT)으로는 찾지 않는다(토큰 조회·폐기 엔드포인트를 열지 않음).
CREATE TABLE oauth2_authorization (
    id                            varchar(100) NOT NULL PRIMARY KEY,
    registered_client_id          text NOT NULL,
    principal_name                varchar(200) NOT NULL,
    authorization_grant_type      varchar(100) NOT NULL,
    authorized_scopes             varchar(1000) DEFAULT NULL,
    attributes                    text DEFAULT NULL,
    state                         varchar(500) DEFAULT NULL,
    authorization_code_value      text DEFAULT NULL,
    authorization_code_issued_at  timestamptz DEFAULT NULL,
    authorization_code_expires_at timestamptz DEFAULT NULL,
    authorization_code_metadata   text DEFAULT NULL,
    access_token_value            text DEFAULT NULL,
    access_token_issued_at        timestamptz DEFAULT NULL,
    access_token_expires_at       timestamptz DEFAULT NULL,
    access_token_metadata         text DEFAULT NULL,
    access_token_type             varchar(100) DEFAULT NULL,
    access_token_scopes           varchar(1000) DEFAULT NULL,
    oidc_id_token_value           text DEFAULT NULL,
    oidc_id_token_issued_at       timestamptz DEFAULT NULL,
    oidc_id_token_expires_at      timestamptz DEFAULT NULL,
    oidc_id_token_metadata        text DEFAULT NULL,
    refresh_token_value           text DEFAULT NULL,
    refresh_token_issued_at       timestamptz DEFAULT NULL,
    refresh_token_expires_at      timestamptz DEFAULT NULL,
    refresh_token_metadata        text DEFAULT NULL,
    user_code_value               text DEFAULT NULL,
    user_code_issued_at           timestamptz DEFAULT NULL,
    user_code_expires_at          timestamptz DEFAULT NULL,
    user_code_metadata            text DEFAULT NULL,
    device_code_value             text DEFAULT NULL,
    device_code_issued_at         timestamptz DEFAULT NULL,
    device_code_expires_at        timestamptz DEFAULT NULL,
    device_code_metadata          text DEFAULT NULL,
    created_at                    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX oauth2_authorization_state_idx ON oauth2_authorization (state);
CREATE INDEX oauth2_authorization_code_idx ON oauth2_authorization (authorization_code_value);
CREATE INDEX oauth2_authorization_refresh_token_idx ON oauth2_authorization (refresh_token_value);

CREATE TABLE oauth2_authorization_consent (
    registered_client_id text NOT NULL,
    principal_name       varchar(200) NOT NULL,
    authorities          varchar(1000) NOT NULL,
    PRIMARY KEY (registered_client_id, principal_name)
);

-- 갱신할 때 바뀐 옛 갱신 토큰. 다시 들어오면 탈취로 보고 그 인가 전체를 지운다(README "액세스 토큰").
-- 들어온 토큰과 같은지만 보면 되므로 토큰 원문 대신 SHA-256 해시만 둔다.
CREATE TABLE oauth2_rotated_refresh_token (
    token_hash       char(64) NOT NULL PRIMARY KEY,
    authorization_id varchar(100) NOT NULL,
    rotated_at       timestamptz NOT NULL
);
