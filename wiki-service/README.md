# 위키 서비스 (M3, 인가 서버는 M5)

문서·권한·등급의 원천입니다(ADR-01). 문서가 바뀌면 같은 트랜잭션에서 아웃박스에 이벤트를 쓰고, 폴러가 Redis Streams로 발행합니다(ADR-09, ADR-19). 검색 서버와 인덱서(Python)는 이 문서의 API와 이벤트 형식만 압니다.

편집 화면은 만들지 않습니다(1장 "범위 밖"). 문서를 고치는 경로는 관리자 API 하나입니다.

## 실행

```bash
docker compose up -d wiki-db redis wiki   # 위키 DB, Redis, 위키 서비스
uv run wiki-rag-seed                       # data/wiki의 가상 위키 200문서를 위키로 옮김
```

로컬 개발용 토큰과 비밀번호는 `docker-compose.yml`의 기본값을 쓰고, 포트는 127.0.0.1에만 엽니다.

| 설정 (환경 변수) | 기본값 | 설명 |
|---|---|---|
| `SPRING_DATASOURCE_URL` | `jdbc:postgresql://127.0.0.1:5434/wiki` | 위키 DB. 검색 DB와 분리한다 (ADR-01) |
| `SPRING_DATA_REDIS_URL` | `redis://127.0.0.1:6380` | 이벤트를 발행할 Redis |
| `WIKI_SERVICE_TOKEN` | 없음 (필수) | 검색 서버·인덱서가 쓰는 서비스 자격 증명 (ADR-06) |
| `WIKI_ADMIN_TOKEN` | 없음 (필수) | 관리자 API 토큰 |
| `WIKI_PUBLIC_URL` | `https://wiki.saesol.example` | 문서 링크(`{WIKI_PUBLIC_URL}/doc/{doc_id}`)의 앞부분 |
| `WIKI_EVENT_PARTITIONS` | `4` | 문서 이벤트 스트림 수. 인덱서와 같은 값이어야 한다 |
| `WIKI_AUTH_ISSUER` | `http://127.0.0.1:8081` | 인가 서버의 발급자. `https://`로 시작하면 운영으로 본다. 끝에 `/`를 붙이면 뜨지 않는다 |
| `WIKI_AUTH_RESOURCES` | `http://127.0.0.1:8000/mcp` | 토큰을 발급해 줄 MCP 서버 주소 목록(쉼표로 구분) |
| `WIKI_AUTH_SIGNING_KEY` | 없음 (시작할 때 새로 만듦) | 토큰 서명용 RSA 개인 키 파일 경로(PKCS#8 PEM). 발급자가 `https://`인데 비어 있으면 뜨지 않는다 |
| `WIKI_AUTH_CIMD_ALLOW_HTTP_LOCALHOST` | `false` | 로컬 점검용. 발급자가 `https://`면 켤 수 없다 |
| `WIKI_AUTH_CLIENTS_{n}_ID`, `WIKI_AUTH_CLIENTS_{n}_REDIRECTURIS` | 없음 | 사전 등록 클라이언트의 id와 돌아갈 주소(쉼표로 구분). `n`은 0부터 차례로 붙인다 |
| `WIKI_AUTH_CLIENTS_{n}_SECRETHASH` | 없음 | 있으면 기밀 클라이언트(`client_secret_basic`), 없으면 공개 클라이언트(`none`). 비밀의 bcrypt 해시만 넣고, 해시가 아닌 값이면 뜨지 않는다 |
| `WIKI_AUTH_CLIENTS_{n}_TIER` | `external` | 토큰의 `client_tier`. `internal`은 기밀 클라이언트만 가질 수 있고, 공개 클라이언트를 `internal`로 두면 뜨지 않는다 |
| `MANAGEMENT_OPENTELEMETRY_TRACING_EXPORT_OTLP_ENDPOINT` | 없음 (보내지 않음) | 추적을 OTLP로 보낼 곳(Tempo). 요청은 모두 추적한다(표본 비율 1.0) |
| `MANAGEMENT_OTLP_METRICS_EXPORT_ENABLED`, `MANAGEMENT_OTLP_METRICS_EXPORT_URL` | `false`, 없음 | 지표를 OTLP로 보낼지와 보낼 곳(Prometheus). 15초마다 보낸다 |

토큰이 비어 있으면 서비스가 뜨지 않습니다. 인증 정보 없이 열린 상태로 뜨는 것보다 실패하는 편이 안전하기 때문입니다. 두 토큰이 같아도 뜨지 않습니다. 검색 서버가 가진 서비스 토큰으로 문서를 고칠 수 있게 되기 때문입니다.

인가 서버 설정의 의미는 아래 "인가 서버" 절에 있습니다. 운영에서 위험한 설정도 뜨지 않게 막습니다. 서명 키가 없으면 다시 시작할 때마다 발급한 토큰이 모두 무효가 되고, http localhost CIMD를 켜 두면 누구나 client_id로 서버 자신의 로컬 포트에 요청을 보내게 할 수 있기 때문입니다. 서명 키는 `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out signing-key.pem`으로 만들고 저장소에 넣지 않습니다.

기본 설정에는 사전 등록 클라이언트가 없습니다. 운영에서 점검용 클라이언트가 저절로 살아 있지 않게 하기 위해서입니다. 로컬 점검용 공개 클라이언트 `wiki-rag-dev`(돌아갈 주소 `http://127.0.0.1/callback`, 루프백이라 포트는 상관없음)는 `docker-compose.yml`이 `WIKI_AUTH_CLIENTS_0_*`로 켭니다. 기밀 클라이언트의 비밀 해시는 `htpasswd -bnBC 10 "" '비밀' | tr -d ':\n'`처럼 만들고, compose 파일에 적을 때는 `$`를 `$$`로 씁니다.

인가 기록과 동의는 위키 DB(`V3` 마이그레이션의 `oauth2_*` 표)에 두고, 만료된 기록은 한 시간마다 지웁니다. 남용 방지의 IP는 Tomcat이 정합니다. `server.forward-headers-strategy: native`라서 내부망 주소(사설·루프백)에서 온 요청의 `X-Forwarded-For`만 믿고, 인터넷에서 직접 보낸 값은 무시합니다.

테스트는 Testcontainers로 compose와 같은 이미지의 PostgreSQL과 Redis를 띄우므로 Docker가 필요합니다.

```bash
cd wiki-service && ./gradlew test
```

OrbStack을 쓰는데 `/var/run/docker.sock`이 다른 Docker(예: Docker Desktop)를 가리키면 Testcontainers가 Docker를 찾지 못합니다. 이때는 `DOCKER_HOST=unix://$HOME/.orbstack/run/docker.sock ./gradlew test`로 돌립니다.

## 권한 모델

설계서 4장과 같은 의미입니다.

- principal은 `user:{id}`, `group:{id}`, 전체 공개 `all` 세 형태입니다. id는 `[A-Za-z0-9._-]+`입니다.
- 문서·스페이스·그룹·사용자 id는 같은 문자로 된 1~64자이고, 점으로만 된 id(`.`, `..`)는 받지 않습니다. URL 경로에서 경로 이동으로 해석돼 그 id로 문서를 가리킬 수 없기 때문입니다.
- 문서는 스페이스 보기 권한과 문서 제한을 **둘 다** 만족해야 보입니다(ADR-21). 사용자의 principal 집합은 `user:{id}`, 소속 그룹의 `group:{id}`, `all`입니다.
- 두 목록 중 하나라도 비어 있으면 아무도 볼 수 없습니다. 제한 없는 문서의 제한 목록은 `["all"]`입니다.
- 문서 등급(`general`, `confidential`)은 스페이스 기본 등급과 문서 등급 중 높은 쪽입니다(ADR-17). 문서 등급은 스페이스 기본값보다 높일 때만 의미가 있습니다.

## revision과 version

- `version`: 내용 버전. 제목이나 본문이 바뀔 때만 오릅니다. 검색 결과의 출처 표기에 씁니다.
- `revision`: 내용·권한·등급이 바뀌거나 문서가 삭제될 때마다 1 오릅니다(ADR-19). 스페이스의 보기 권한이나 기본 등급이 바뀌면 그 스페이스의 모든 문서가 같은 트랜잭션에서 1씩 오릅니다.
- `updated_at`: 내용이 바뀐 시각. 권한·등급 변경으로는 바뀌지 않습니다("최근 바뀐 문서"는 내용 변경을 뜻함).
- 삭제는 행을 지우지 않고 삭제 표시만 남깁니다(tombstone). 삭제된 문서의 revision을 알아야 늦게 도착한 옛 이벤트를 버릴 수 있기 때문입니다.

## API

JSON 필드 이름은 snake_case, 시각은 ISO 8601(UTC, 예: `2026-09-27T03:00:00Z`)입니다. 위키에서 고친 문서는 마이크로초까지 나옵니다(예: `2026-09-27T13:51:29.822225Z`). 인증은 `Authorization: Bearer {토큰}`이고, 토큰이 없거나 틀리면 본문 없이 401입니다. 오류 본문은 `{"error": "not_found" | "conflict" | "bad_request", "message": "..."}`입니다. 계약에 드는 오류는 이 세 가지뿐이고, 405(허용하지 않는 메서드), 415(JSON이 아닌 본문), 500(서버 오류)은 Spring Boot 기본 오류 본문으로 답합니다.

요청 본문에 모르는 필드가 있으면 400입니다. 오타가 난 권한 필드(`restrictions`, `restrictedPrincipals`)를 무시하면 제한을 뺀 것으로 읽혀 제한 없는 문서가 만들어지기 때문입니다. 같은 id가 이미 있어 DB가 거절한 쓰기는 409, 저장할 수 없는 값(본문의 NUL 문자 등)은 400입니다.

### 내부 API (`/internal/**`, 서비스 토큰)

검색 서버와 인덱서가 부릅니다. 모두 읽기 전용이고 캐시를 거치지 않습니다.

문서 상태(DocumentState)는 아래 형태입니다. `space_principals`는 읽는 시점의 스페이스 보기 권한이고, `classification`은 스페이스 기본값을 반영한 최종 등급입니다.

```json
{
  "doc_id": "infra-011", "title": "운영 DB 접근 절차", "space": "infra",
  "url": "https://wiki.saesol.example/doc/infra-011",
  "version": 3, "revision": 5, "updated_at": "2026-09-01T02:00:00Z",
  "body": "# 운영 DB 접근 절차\n...",
  "space_principals": ["group:eng", "group:infra"],
  "restricted_principals": ["group:dba"],
  "classification": "confidential",
  "deleted": false
}
```

| 요청 | 응답 | 쓰는 곳 |
|---|---|---|
| `GET /internal/spaces` | `{"spaces": [{"space": "infra", "title": "인프라"}]}` | 맥락 헤더의 스페이스 이름 |
| `GET /internal/documents?after={doc_id}&limit={n}` | `{"documents": [DocumentState...], "next": "{doc_id}" 또는 null}`. 삭제되지 않은 문서만, doc_id 순. limit 기본 100, 최대 500 | 전체 색인 |
| `GET /internal/documents/{doc_id}` | DocumentState. 삭제된 문서는 `{"doc_id": "...", "revision": 7, "deleted": true}`, 한 번도 없던 문서는 404 | 인덱서의 상태 재조회 (ADR-19) |
| `GET /internal/revisions` | `{"documents": [{"doc_id": "...", "revision": 5, "deleted": false}]}`. 삭제된 문서 포함 | 야간 정합성 배치 |
| `GET /internal/users/{user_id}/groups` | `{"user_id": "jiho", "groups": ["group:employees", "group:eng"]}`. 모르는 사용자는 404 | 검색 시 그룹 해석 (ADR-08). 검색 서버는 Redis에 60초 캐시하고, 404는 캐시하지 않고 오류로 돌려준다 |
| `GET /internal/users/{user_id}/documents/{doc_id}` | 사용자가 볼 수 있으면 DocumentState, 아니면 404. 없는 문서, 삭제된 문서, 권한 없는 문서, 모르는 사용자의 응답이 모두 같다 | `get_document`와 리소스의 권한 재확인 (ADR-07). 그룹 캐시를 거치지 않는다 |

### 관리자 API (`/admin/**`, 관리자 토큰)

문서를 고치는 모든 요청은 문서 변경과 아웃박스 기록을 한 트랜잭션에서 합니다. 같은 문서를 동시에 고치면 행 잠금으로 차례를 정해 revision이 겹치지 않게 합니다.

| 요청 | 본문 | 결과 | 이벤트 |
|---|---|---|---|
| `POST /admin/import` | `{"spaces": [...], "groups": [...], "users": [...], "documents": [...]}` (아래) | 문서가 하나라도 있으면 409. 가상 위키를 처음 옮길 때만 쓴다 | 문서마다 `CONTENT_CHANGED` |
| `POST /admin/documents` | `{"doc_id", "space", "title", "body", "restricted_principals"?, "classification"?}` | 201, DocumentState. version 1, revision 1. 제한을 빼면 `["all"]` | `CONTENT_CHANGED` |
| `PUT /admin/documents/{doc_id}` | `{"title", "body", "base_version"}` | base_version이 현재 version과 다르면 409. version·revision +1, updated_at 갱신 | `CONTENT_CHANGED` |
| `PUT /admin/documents/{doc_id}/restrictions` | `{"principals": [...]}` | revision +1 | `ACL_CHANGED` |
| `PUT /admin/documents/{doc_id}/classification` | `{"classification": "general" 또는 "confidential" 또는 null}`. 키가 빠지면 400 | 문서 등급 지정(null이면 스페이스 기본값을 따름). revision +1 | `ACL_CHANGED` |
| `DELETE /admin/documents/{doc_id}` | 없음 | 삭제 표시, revision +1. 이미 삭제된 문서는 404 | `DELETED` |
| `PUT /admin/spaces/{space}/viewers` | `{"principals": [...]}` | 스페이스의 삭제되지 않은 문서마다 revision +1 | 문서마다 `ACL_CHANGED` |
| `PUT /admin/spaces/{space}/classification` | `{"classification": "general" 또는 "confidential"}` | 위와 같음 | 문서마다 `ACL_CHANGED` |
| `PUT /admin/users/{user_id}` | `{"name"}` | 사용자 생성(있으면 이름만 바꿈) | 없음 |
| `PUT /admin/groups/{group}/members/{user_id}` | 없음 | 그룹에 사용자 추가 | `MEMBERSHIP_CHANGED` |
| `DELETE /admin/groups/{group}/members/{user_id}` | 없음 | 그룹에서 사용자 제거 | `MEMBERSHIP_CHANGED` |

로그인 비밀번호를 정하는 `PUT /admin/users/{user_id}/password`는 아래 "인가 서버"의 "로그인"에 있습니다. 이벤트는 없고, 12자보다 짧거나 bcrypt가 쓰는 길이(UTF-8 72바이트)를 넘으면 400입니다. 72바이트 뒤를 잘라 저장하면 뒷부분이 다른 비밀번호도 통하게 되기 때문입니다.

없는 문서·스페이스·그룹·사용자는 404, 형식이 틀린 id·principal·등급은 400입니다. 사용자 문서 조회(`/internal/users/{user_id}/documents/{doc_id}`)도 id 형식이 틀리면 400인데, id 형식은 공개된 규칙이라 문서의 존재를 드러내지 않습니다.

응답 본문은 아래와 같습니다.

- 가져오기: 200, `{"spaces": n, "groups": n, "users": n, "documents": n}`
- 문서 생성: 201, 문서 수정·제한·등급 변경: 200. 모두 바뀐 뒤의 DocumentState
- 문서 삭제: 200, 삭제 표시(`{"doc_id", "revision", "deleted": true}`)
- 스페이스 변경: 200, `{"space", "title", "principals", "classification", "documents_changed"}`
- 사용자·멤버십 변경: 200, `{"user_id", "name", "groups": ["group:..."]}`

그 밖의 규칙입니다.

- 삭제된 문서의 id로 새 문서를 만들 수 없습니다(409). 새 문서는 revision 1부터 시작하는데, 인덱서는 삭제 때의 더 큰 revision을 기억하고 있어 새 문서의 이벤트를 옛 이벤트로 보고 버리기 때문입니다.
- 이미 속한 그룹에 추가하거나 속하지 않은 그룹에서 빼면 200이고 이벤트를 내지 않습니다. 바뀐 것이 없기 때문입니다. 멤버십 변경은 인덱스를 바꾸지 않고 검색 서버의 그룹 캐시만 무효화합니다(ADR-08, 4장 "권한 변경 전파").

`POST /admin/import` 본문의 각 항목은 아래와 같습니다. 가상 위키의 doc_id, version, revision, updated_at을 그대로 옮겨 골든셋과 평가 결과가 계속 맞게 합니다.

```json
{
  "spaces": [{"space": "infra", "title": "인프라", "principals": ["group:infra", "group:eng"], "classification": "general"}],
  "groups": [{"group": "dba", "name": "DB 관리자"}],
  "users": [{"user_id": "taeyang", "name": "taeyang", "groups": ["employees", "eng", "infra", "dba"]}],
  "documents": [{"doc_id": "infra-011", "space": "infra", "title": "...", "body": "...", "version": 3, "revision": 5,
                 "updated_at": "2026-09-01T02:00:00Z", "restricted_principals": ["group:dba"], "classification": "confidential"}]
}
```

## 인가 서버 (M5, ADR-24)

MCP 서버(리소스 서버)에 붙는 사용자가 누구인지 정하는 OAuth 2.1 인가 서버입니다. 로그인한 위키 사용자에게 MCP 서버 전용 액세스 토큰을 발급합니다. MCP 서버(Python)는 이 절의 토큰 형식과 메타데이터만 압니다.

### 설정

| 설정 (환경 변수) | 로컬 기본값 | 설명 |
|---|---|---|
| `WIKI_AUTH_ISSUER` | `http://127.0.0.1:8081` | 발급자(`iss`). 운영은 `https://auth.dmssh.store`. 메타데이터의 `issuer`와 정확히 같다 |
| `WIKI_AUTH_RESOURCES` | `http://127.0.0.1:8000/mcp` | 토큰을 발급해 줄 리소스(MCP 서버) 주소 목록, 쉼표로 구분. 운영은 `https://mcp.dmssh.store/mcp` |
| `WIKI_AUTH_SIGNING_KEY` | 없음 (로컬은 시작할 때 새로 만듦) | 토큰 서명용 RSA 개인 키(PEM) 파일 경로. 운영에서 비어 있으면 뜨지 않는다. 다시 시작할 때 키가 바뀌면 발급한 토큰이 모두 무효가 되기 때문이다 |
| `WIKI_AUTH_CIMD_ALLOW_HTTP_LOCALHOST` | `false` | 로컬 점검용. 켜면 `http://localhost`의 CIMD 문서도 받는다 |

### 공개 경로

아래 경로만 토큰 없이 열립니다. 운영의 리버스 프록시(Caddy)도 인가 서버 호스트에서 이 경로만 넘깁니다. 내부 API와 관리자 API는 지금처럼 서비스 토큰과 관리자 토큰이 필요하고, 목록에 없는 경로는 모두 막습니다.

| 경로 | 용도 |
|---|---|
| `GET /.well-known/oauth-authorization-server` | 인가 서버 메타데이터 (RFC 8414) |
| `GET /oauth2/authorize` | 인가 요청. 로그인하지 않았으면 로그인 화면으로 보낸다 |
| `POST /oauth2/token` | 코드와 갱신 토큰을 액세스 토큰으로 바꾼다 |
| `GET /oauth2/jwks` | 토큰 서명 검증용 공개 키 |
| `GET, POST /login` | 로그인 화면 (위키 사용자 id와 비밀번호) |
| `GET, POST /oauth2/consent` | 동의 화면 |

메타데이터에는 최소한 아래 값이 있습니다.

```json
{
  "issuer": "https://auth.dmssh.store",
  "authorization_endpoint": "https://auth.dmssh.store/oauth2/authorize",
  "token_endpoint": "https://auth.dmssh.store/oauth2/token",
  "jwks_uri": "https://auth.dmssh.store/oauth2/jwks",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
  "scopes_supported": ["wiki:read"],
  "client_id_metadata_document_supported": true,
  "authorization_response_iss_parameter_supported": true
}
```

`registration_endpoint`는 없습니다. 동적 등록(DCR)을 열지 않기 때문입니다(ADR-24).

### 액세스 토큰

RS256으로 서명한 JWT이고, 수명은 10분입니다. 서명 키는 `jwks_uri`의 `kid`로 찾습니다.

| 클레임 | 값 |
|---|---|
| `iss` | `WIKI_AUTH_ISSUER` |
| `sub` | 위키 사용자 id (`jiho`처럼 `user:` 접두사 없이) |
| `aud` | 인가 요청의 `resource` 하나 (`WIKI_AUTH_RESOURCES` 중 하나) |
| `client_id` | 클라이언트 id. CIMD 클라이언트는 메타데이터 문서 주소 |
| `client_tier` | `internal` 또는 `external`. 인가 서버가 클라이언트 설정으로 정한다(아래 "클라이언트") |
| `scope` | `wiki:read` |
| `exp`, `iat`, `jti` | 표준 의미 |

MCP 서버는 서명, `iss`, `aud`(자기 주소), `exp`, `scope`를 모두 검증하고, `sub`를 위키 사용자 id로 쓰고, 신뢰 등급은 `client_tier`로 정합니다(ADR-17). `client_tier`가 없거나 모르는 값이면 외부로 봅니다. MCP 서버는 등급을 정하는 목록을 따로 두지 않습니다. 토큰을 위키에 그대로 넘기지 않고, 위키 호출은 지금처럼 서비스 토큰으로 합니다(ADR-06).

갱신 토큰은 공개 클라이언트에도 주고, 쓸 때마다 새 것으로 바꿉니다(OAuth 2.1 4.3.1). 이미 바꾼 옛 토큰이 다시 들어오면 탈취로 보고 그 인가 전체(살아 있는 갱신 토큰 포함)를 무효로 합니다. 갱신 토큰 하나의 수명은 7일이고, 갱신을 이어 가도 처음 로그인에서 30일이 지나면 더 발급하지 않습니다. 인가 기록과 동의는 위키 DB에 두어 위키를 다시 시작해도 유지되고, 만료된 기록은 주기적으로 지웁니다.

### 토큰 대상 지정 (RFC 8707)

- 인가 요청과 코드 교환 요청에 `resource`가 있어야 하고, 값이 `WIKI_AUTH_RESOURCES` 중 하나와 정확히 같아야 합니다. 없거나 다르면 `invalid_target`으로 거절합니다.
- 코드 교환의 `resource`는 인가 요청 때의 값과 같아야 합니다. 갱신 요청에서는 생략할 수 있고, 있으면 처음 값과 같아야 합니다. 갱신할 때도 처음 값이 지금의 `WIKI_AUTH_RESOURCES`에 남아 있어야 합니다.

### 클라이언트

클라이언트는 두 종류뿐입니다.

- **사전 등록**: 우리가 만든 클라이언트(데모 에이전트, 부하 테스트, 점검 도구)입니다. 설정에 id와 돌아갈 주소를 적습니다. 모두 PKCE가 필수이고, 두 종류가 있습니다.
  - 공개 클라이언트(`none`): 사용자 기기에서 도는 것. 등급은 항상 `external`입니다. id가 비밀이 아니어서 다른 앱도 같은 id로 인가 흐름을 시작할 수 있기 때문입니다(RFC 8252 8.6).
  - 기밀 클라이언트(`client_secret_basic`): 서버에서 도는 것(데모 에이전트). 설정에는 비밀의 bcrypt 해시만 두고, 등급을 `internal`로 정할 수 있습니다. 공개 클라이언트를 `internal`로 설정하면 위키가 뜨지 않습니다.
- **CIMD**: `client_id`가 `https://`로 시작하면 그 주소의 메타데이터 문서를 가져와 클라이언트로 씁니다. 등급은 항상 `external`입니다. 아래를 모두 지켜야 받습니다.
  - `client_id`는 2048자 이하입니다. 로그에 남길 때는 제어 문자를 이스케이프합니다.
  - 주소는 `https`이고, 호스트를 해석한 IP가 모두 공인 주소입니다(사설, 루프백, 링크 로컬 등은 거절). 연결은 검사한 IP로만 합니다. 검사 뒤 DNS 답이 바뀌어 내부 주소로 붙는 것을 막기 위해서입니다.
  - 주소에 프래그먼트, 사용자 정보, `.`이나 `..` 경로 세그먼트가 없습니다.
  - 리다이렉트를 따라가지 않고, DNS 조회부터 응답을 다 읽기까지 전체 3초 안에 5KB 이하의 `application/json` 200 응답이어야 합니다. 3초가 지나면 TLS 핸드셰이크 중이라도 연결을 끊습니다.
  - 동시에 가져오는 문서는 4개까지입니다. 자리가 없으면 기다리지 않고 거절합니다. 가져오기에 실패한 주소는 5분 동안 다시 가져오지 않고 거절합니다. 요청 하나에서 같은 문서를 두 번 가져오지 않습니다. 로그인 없이 누구나 가져오기를 일으킬 수 있어, 느린 서버로 위키의 요청 스레드를 붙잡는 공격을 막기 위해서입니다.
  - 문서의 `client_id`가 가져온 주소와 정확히 같습니다.
  - `redirect_uris`가 있고, `token_endpoint_auth_method`는 없거나 `none`입니다. `client_secret` 계열 필드가 있으면 거절합니다.
  - 인가 요청의 `redirect_uri`는 문서의 값과 정확히 같아야 합니다. 루프백 주소(`http://localhost`, `http://127.0.0.1`)만 포트를 무시하고 비교합니다(RFC 8252 7.3). Claude Code가 세션마다 다른 포트를 쓰기 때문입니다.
  - 가져온 문서는 최대 24시간 캐시합니다. 가져오기에 실패하면 캐시된 옛 문서로 대신하지 않고 거절합니다.
  - 사용자가 로그인하기 전에 인가 요청이 잘못됐으면(`resource`나 PKCE 누락 등) 문서의 `redirect_uri`로 돌려보내지 않고 400 오류 화면으로 끝냅니다. 누구나 CIMD 문서에 아무 주소나 적을 수 있어, 돌려보내면 이 인가 서버가 피싱 사이트로 보내는 링크가 되기 때문입니다(RFC 9700 4.11.2).

동의 화면에는 `client_id`의 호스트를 크게, 문서가 주장하는 `client_name`은 그 아래 작게 보여 줍니다. 이름은 누구나 적을 수 있지만 호스트는 그 도메인을 가진 쪽만 쓸 수 있기 때문입니다. `client_id` 전체 주소와, 허용하면 실제로 돌아갈 `redirect_uri`의 호스트도 함께 보여 주고, 두 호스트가 다르면 경고를 붙입니다. 단 돌아갈 곳이 루프백 주소면 경고 대신 "이 컴퓨터에서 실행 중인 앱"으로 보여 줍니다. 그 코드는 사용자 자신의 컴퓨터에 있는 앱만 받고, Claude Code처럼 앱 주소(`claude.ai`)와 돌아갈 곳(`localhost`)이 늘 다른 네이티브 앱이 매번 경고를 보지 않게 하기 위해서입니다. 공용 저장소 호스트에 문서를 올려 믿을 만한 호스트로 보이게 하는 경우를 사용자가 알아볼 수 있게 하기 위해서입니다.

동의는 비밀로 인증하는 기밀 클라이언트에만 기억합니다. 같은 사용자가 이미 허용한 범위면 다음 인가에서 동의 화면을 건너뜁니다. 공개 클라이언트(사전 등록 공개 클라이언트와 CIMD)는 이전에 허용했어도 새 인가마다 동의 화면을 보여 줍니다(RFC 8252 8.6). 공개 `client_id`는 누구나 쓸 수 있어서, 동의를 기억하면 로그인 세션이 살아 있는 동안 같은 id를 쓴 다른 프로그램이 화면 하나 없이 코드를 받아 가기 때문입니다. 갱신 토큰으로 이어 쓰는 동안에는 동의 화면이 나오지 않습니다.

### 남용 방지

- 로그인 실패는 같은 IP에서 1분에 10번, 같은 사용자 id에 1분에 10번을 넘으면 429로 거절합니다. 인가 요청(`GET /oauth2/authorize`)은 같은 IP에서 1분에 60번까지입니다. 운영에서 IP는 리버스 프록시(Caddy)가 넣은 `X-Forwarded-For`로 판단하고, 그 헤더는 내부망의 프록시에서 온 것만 믿습니다.
- 세션은 로그인 전 요청에도 생기므로(돌아갈 요청을 기억해야 함) 수에 상한을 둡니다. 상한을 넘으면 새 세션을 만들지 않고 거절합니다.
- 세션 쿠키는 `SameSite=Lax`이고, 로그인·동의 화면에는 CSP(`default-src 'none'; style-src 'unsafe-inline'`)와 `X-Frame-Options: DENY`를 붙입니다.

### 로그인

위키 사용자 id와 비밀번호로 로그인합니다. 비밀번호는 bcrypt 해시로 `users.password_hash`에 두고, 해시가 없는 사용자는 로그인할 수 없습니다. 비밀번호는 관리자 API로만 정합니다.

| 요청 | 본문 | 결과 |
|---|---|---|
| `PUT /admin/users/{user_id}/password` | `{"password"}` (12자 이상) | 204. 없는 사용자는 404 |

## 이벤트

아웃박스 행은 문서 변경과 같은 트랜잭션에서 씁니다. 폴러가 0.5초마다 발행 안 된 행을 최대 100개씩 `FOR UPDATE SKIP LOCKED`로 잡아 Redis에 `XADD`하고 발행 시각을 적습니다. `XADD` 뒤 커밋 전에 죽으면 같은 이벤트가 다시 나가는데, 인덱서가 revision으로 걸러 결과가 같습니다(ADR-19).

| 스트림 | 필드 | 설명 |
|---|---|---|
| `wiki:events:{p}` | `doc_id`, `revision`, `type`(`CONTENT_CHANGED`, `ACL_CHANGED`, `DELETED`), `outbox_id`, `created_at`, `traceparent`(있을 때만) | 문서 이벤트. `p = CRC32(doc_id의 UTF-8 바이트) mod WIKI_EVENT_PARTITIONS` |
| `wiki:membership` | `user_id`, `type`(`MEMBERSHIP_CHANGED`), `outbox_id`, `created_at`, `traceparent`(있을 때만) | 그룹 캐시 무효화용. 인덱서 워커가 문서 이벤트보다 먼저 읽어 검색 서버의 그룹 캐시를 무효화하고, 처리한 이벤트는 `XACKDEL ... ACKED`로 지운다 |

- 이벤트 형식은 ADR-09의 `(doc_id, version, type)`이고, version 자리에 revision을 담습니다(ADR-19). 필드 이름은 헷갈리지 않게 `revision`으로 씁니다.
- `created_at`은 아웃박스 행을 쓴 시각입니다. 인덱싱 지연(5장 "측정 지표")을 여기서부터 잽니다.
- `traceparent`는 변경을 일으킨 요청의 추적 맥락입니다([W3C Trace Context](https://www.w3.org/TR/trace-context/) 형식, ADR-15). 아웃박스 행을 쓸 때 함께 적어 둡니다. 폴러는 나중에 다른 스레드에서 발행하므로 발행할 때는 요청의 맥락이 없기 때문입니다. 인덱서는 이 값을 부모로 처리 스팬을 열어, 편집부터 검색 반영까지를 한 추적으로 봅니다. 요청 밖의 변경(추적 중이 아닐 때)에는 넣지 않습니다. 없거나 형식이 틀려도 처리는 같고 추적만 새로 시작합니다.
- 문서 id로 스트림을 나누는 이유는 같은 문서의 이벤트를 소비자 하나가 차례로 처리하게 하기 위해서입니다(ADR-19). 파티션 계산은 Python 쪽(`zlib.crc32`)과 같아야 하므로 양쪽 테스트에 같은 값을 둡니다. `co-001`은 1, `eng-022`는 1, `infra-011`은 3, `hr-007`은 0, `fin-011`은 0, `data-003`은 1입니다(파티션 4개).
- 폴러는 스트림 길이를 자르지 않습니다. 인덱서가 처리를 마친 이벤트를 `XACKDEL ... ACKED`로 확인과 동시에 지워, 스트림에는 처리 안 된 이벤트만 남습니다. 발행 시점에 길이로 자르면 인덱서가 멈춘 동안 쌓인 이벤트가 처리 전에 지워질 수 있습니다.
- 발행한 아웃박스 행은 7일 뒤 지웁니다. 발행 기록을 며칠 남겨 두면 장애를 조사할 때 "위키가 이벤트를 냈는가"를 확인할 수 있습니다.
