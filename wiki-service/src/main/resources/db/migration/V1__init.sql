-- 위키 서비스 스키마. 문서·권한·등급의 원천이다 (ADR-01).
--
-- id 열은 모두 COLLATE "C"로 둔다. 전체 색인의 doc_id 순서와 페이지 경계(after)가 DB 로캘에 따라 달라지지 않고,
-- Python 쪽 sorted()와 같은 바이트 순서가 되게 하기 위해서다.
-- principal 목록은 행 집합으로 저장하고, 빈 집합은 "아무도 볼 수 없음"으로 읽는다 (4장 "권한 모델").
-- 형식 검사는 API에서 하지만, 권한 데이터라 DB에도 같은 조건을 걸어 둔다.

CREATE TABLE spaces (
    id                     text COLLATE "C" PRIMARY KEY,
    title                  text NOT NULL,
    default_classification text NOT NULL CHECK (default_classification IN ('GENERAL', 'CONFIDENTIAL'))
);

CREATE TABLE space_viewers (
    space_id  text COLLATE "C" NOT NULL REFERENCES spaces (id),
    principal text NOT NULL CHECK (principal ~ '^(all|(user|group):[A-Za-z0-9._-]+)$'),
    PRIMARY KEY (space_id, principal)
);

CREATE TABLE users (
    id   text COLLATE "C" PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE groups (
    id   text COLLATE "C" PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE group_members (
    user_id  text COLLATE "C" NOT NULL REFERENCES users (id),
    group_id text COLLATE "C" NOT NULL REFERENCES groups (id),
    PRIMARY KEY (user_id, group_id)
);

-- revision은 내용·권한·등급·삭제마다 오르는 번호이고, version은 내용 버전이다 (ADR-19).
-- 삭제는 행을 지우지 않는다. 삭제된 문서의 revision을 알아야 늦게 온 옛 이벤트를 버릴 수 있다.
CREATE TABLE documents (
    id             text COLLATE "C" PRIMARY KEY,
    space_id       text COLLATE "C" NOT NULL REFERENCES spaces (id),
    title          text NOT NULL,
    body           text NOT NULL,
    version        integer NOT NULL CHECK (version >= 1),
    revision       bigint NOT NULL CHECK (revision >= 1),
    classification text CHECK (classification IN ('GENERAL', 'CONFIDENTIAL')),  -- NULL이면 스페이스 기본값을 따른다
    deleted        boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL,
    updated_at     timestamptz NOT NULL
);

CREATE INDEX documents_space_idx ON documents (space_id);

CREATE TABLE document_restrictions (
    document_id text COLLATE "C" NOT NULL REFERENCES documents (id),
    principal   text NOT NULL CHECK (principal ~ '^(all|(user|group):[A-Za-z0-9._-]+)$'),
    PRIMARY KEY (document_id, principal)
);

-- 트랜잭셔널 아웃박스 (ADR-09). 문서 변경과 같은 트랜잭션에서 쓰고, 폴러가 Redis Streams로 발행한다.
-- 문서 이벤트는 aggregate_id가 doc_id이고 revision을 담는다. 멤버십 이벤트는 aggregate_id가 user_id이고 revision이 없다.
CREATE TABLE outbox (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    aggregate_type text NOT NULL CHECK (aggregate_type IN ('DOCUMENT', 'USER')),
    aggregate_id   text NOT NULL,
    revision       bigint,
    event_type     text NOT NULL CHECK (event_type IN ('CONTENT_CHANGED', 'ACL_CHANGED', 'DELETED', 'MEMBERSHIP_CHANGED')),
    created_at     timestamptz NOT NULL,
    published_at   timestamptz
);

-- 폴러는 발행 안 된 행만 id 순으로 읽는다. 발행한 행은 7일 동안 남으므로 부분 인덱스로 미발행 행만 담는다.
CREATE INDEX outbox_unpublished_idx ON outbox (id) WHERE published_at IS NULL;
