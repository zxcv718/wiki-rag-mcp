# 위키 서비스 (M3)

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

토큰이 비어 있으면 서비스가 뜨지 않습니다. 인증 정보 없이 열린 상태로 뜨는 것보다 실패하는 편이 안전하기 때문입니다. 두 토큰이 같아도 뜨지 않습니다. 검색 서버가 가진 서비스 토큰으로 문서를 고칠 수 있게 되기 때문입니다.

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

## 이벤트

아웃박스 행은 문서 변경과 같은 트랜잭션에서 씁니다. 폴러가 0.5초마다 발행 안 된 행을 최대 100개씩 `FOR UPDATE SKIP LOCKED`로 잡아 Redis에 `XADD`하고 발행 시각을 적습니다. `XADD` 뒤 커밋 전에 죽으면 같은 이벤트가 다시 나가는데, 인덱서가 revision으로 걸러 결과가 같습니다(ADR-19).

| 스트림 | 필드 | 설명 |
|---|---|---|
| `wiki:events:{p}` | `doc_id`, `revision`, `type`(`CONTENT_CHANGED`, `ACL_CHANGED`, `DELETED`), `outbox_id`, `created_at` | 문서 이벤트. `p = CRC32(doc_id의 UTF-8 바이트) mod WIKI_EVENT_PARTITIONS` |
| `wiki:membership` | `user_id`, `type`(`MEMBERSHIP_CHANGED`), `outbox_id`, `created_at` | 그룹 캐시 무효화용. 인덱서 워커가 문서 이벤트보다 먼저 읽어 검색 서버의 그룹 캐시를 무효화하고, 처리한 이벤트는 `XACKDEL ... ACKED`로 지운다 |

- 이벤트 형식은 ADR-09의 `(doc_id, version, type)`이고, version 자리에 revision을 담습니다(ADR-19). 필드 이름은 헷갈리지 않게 `revision`으로 씁니다.
- `created_at`은 아웃박스 행을 쓴 시각입니다. 인덱싱 지연(5장 "측정 지표")을 여기서부터 잽니다.
- 문서 id로 스트림을 나누는 이유는 같은 문서의 이벤트를 소비자 하나가 차례로 처리하게 하기 위해서입니다(ADR-19). 파티션 계산은 Python 쪽(`zlib.crc32`)과 같아야 하므로 양쪽 테스트에 같은 값을 둡니다. `co-001`은 1, `eng-022`는 1, `infra-011`은 3, `hr-007`은 0, `fin-011`은 0, `data-003`은 1입니다(파티션 4개).
- 폴러는 스트림 길이를 자르지 않습니다. 인덱서가 처리를 마친 이벤트를 `XACKDEL ... ACKED`로 확인과 동시에 지워, 스트림에는 처리 안 된 이벤트만 남습니다. 발행 시점에 길이로 자르면 인덱서가 멈춘 동안 쌓인 이벤트가 처리 전에 지워질 수 있습니다.
- 발행한 아웃박스 행은 7일 뒤 지웁니다. 발행 기록을 며칠 남겨 두면 장애를 조사할 때 "위키가 이벤트를 냈는가"를 확인할 수 있습니다.
