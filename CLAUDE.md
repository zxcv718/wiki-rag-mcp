# wiki-rag-mcp

사내 위키 문서를 **사용자 권한에 맞게** 검색해, LLM 에이전트가 MCP 도구로 호출할 수 있게 하는 서버입니다.

- 전체 설계: `docs/design.md`
- 결정 기록: `docs/adr/` (목록은 `docs/adr/README.md`)

설계와 다른 방향으로 구현해야 할 때는 코드를 먼저 바꾸지 말고, 어느 ADR과 충돌하는지 먼저 알려 주세요.

## 현재 단계

**M1 완료**: Python MCP 서버(도구 3개와 문서 리소스), stdio 연동, 가상 위키 200문서(`data/wiki`), 권한·등급 테스트셋.

**M2 완료**: 골든셋 300문항, 평가 스크립트, ADR-02·11·12·13 판정 실험

- 골든셋은 `data/golden/`, 태그 `golden-v1`로 고정했습니다. 판정 계획과 결과는 `experiments/m2-judgement/README.md`에 있고, 평가는 `wiki-rag-eval`로 돌립니다.
- 판정 결과: 맥락 헤더 유지, 하이브리드 보류로 검색 저장소를 PostgreSQL + pgvector로 단순화(ADR-22), 리랭커는 지연 예산 초과로 도입하지 않음, 상용 임베딩(gemini-embedding-001)은 기각해 로컬 bge-m3 유지. 판정 코드와 결과는 태그 `m2-judgement`에 있습니다.
- 검색 저장소는 PostgreSQL + pgvector입니다(`search/pg_store.py`). 로컬 DB는 `docker compose up -d postgres`로 띄우고 127.0.0.1:5433에 엽니다(5432는 다른 프로젝트와 겹치지 않게 비워 둠). OpenSearch와 하이브리드·Gemini 비교 코드는 판정 뒤 지웠고, 재현은 태그 `m2-judgement`로 합니다.
- 권한 필터는 두 필드를 배열 겹침(`&&`)으로 WHERE 절에 겁니다. HNSW가 필터를 스캔 뒤에 적용하므로 검색마다 `hnsw.iterative_scan = strict_order`를 켜고, 결과 수 테스트로 k개가 나오는지 확인합니다(4장 "벡터 검색의 필터 적용 방식").
- 판정 뒤 새로 드러난 과제(최근 문서 우선, 경량 리랭커)는 판정 기준을 먼저 적은 뒤 별도 실험으로 다룹니다. 결과를 본 뒤 바로 설정을 바꾸지 않습니다.

**M3 완료**: Spring 위키 서비스(`wiki-service/`), 아웃박스, Redis Streams, 증분 인덱싱 워커

- 위키 API와 이벤트 형식의 계약은 `wiki-service/README.md`입니다. 위키(Java)와 인덱서(Python)는 이 계약만 알고, 한쪽을 바꿀 때는 계약부터 고칩니다.
- 로컬 실행: `docker compose up -d`(검색 DB 5433, 위키 DB 5434, Redis 6380, 위키 8081), `uv run wiki-rag-seed`(가상 위키 200문서를 위키로 옮김, 빈 위키에서만), `uv run wiki-rag-worker`(이벤트 소비), `uv run wiki-rag-reconcile`(야간 정합성 배치, `--dry-run` 가능). 위키 테스트는 `cd wiki-service && ./gradlew test`이고, OrbStack이면 `DOCKER_HOST=unix://$HOME/.orbstack/run/docker.sock`이 필요할 수 있습니다.
- 인덱서는 이벤트를 신호로만 쓰고 위키에서 상태를 다시 읽습니다. 반영 순서는 권한·등급(먼저 커밋), 바뀐 섹션만 임베딩, 청크 교체와 `doc_state` 기록(한 트랜잭션)입니다(ADR-19, ADR-10). 이 순서를 바꾸면 `tests/test_incremental.py`의 시나리오 테스트가 잡습니다.
- 측정(`experiments/m3-indexing`): 수정 후 검색 반영 p95는 평상시 1.1초 이하, 새 문서 20개를 한꺼번에 만들 때 8.05초로 목표(30초) 안입니다. 임베딩 절감률은 섹션 하나 수정 82.6%, 권한·등급 변경 100%, 제목 변경 0%입니다.

**M4 완료**: 검색 서버를 위키 API에 연결, 그룹 캐시, 권한 테스트셋을 위키 서비스로 확장, CI

- 검색 서버는 기본으로 위키 API를 씁니다(`WIKI_SOURCE=wiki`). 그룹은 `GET /internal/users/{id}/groups`를 Redis에 60초 캐시하고(`auth/groups.py`), 본문은 `GET /internal/users/{id}/documents/{doc}`로 위키가 권한을 다시 판단해 줍니다. 파일 위키는 `WIKI_SOURCE=file`로 평가·테스트에만 씁니다. 개발용 `.mcp.json`도 위키 모드라서 `docker compose up -d`, `uv run wiki-rag-seed`, `uv run wiki-rag-worker`가 떠 있어야 합니다.
- 그룹 캐시는 사용자별 세대 번호로 무효화합니다. 키만 지우면 무효화 직전에 읽은 옛 그룹이 다시 캐시됩니다(stale set). 인덱서 워커가 `wiki:membership`을 문서 이벤트보다 먼저 읽어 번호를 올립니다. 위키에 없는 사용자는 오류로 돌려주고 캐시하지 않습니다. 세부와 이유는 설계서 4장 "구현 세부 (M4)"입니다.
- 권한 테스트셋(`tests/test_permissions_integration.py`)은 파일 위키와 위키 서비스 양쪽에서 돕니다. 권한 회수 종단 테스트는 `tests/test_wiki_permissions_e2e.py`입니다. 통합 테스트는 서비스가 없으면 건너뛰지만 `REQUIRE_SERVICES=1`이면 실패합니다(CI).
- CI는 `.github/workflows/ci.yml`입니다. Python 테스트와 권한 테스트셋, 골든셋 회귀 검사(`uv run wiki-rag-eval regress`, Recall@5가 `data/golden/baseline.json`보다 2%p 이상 떨어지거나 권한 위반이 1건이면 실패), 위키 서비스 테스트를 돌립니다. 회귀 검사는 임베딩을 `.cache/ci-embeddings.npz`에 캐시합니다. 저장소는 공개 `zxcv718/wiki-rag-mcp`이고, 캐시가 있을 때 CI 전체가 약 4분입니다(임베딩 캐시가 모두 버려지는 PR은 골든셋 작업이 15분쯤).
- 측정(`experiments/m4-permissions`): 권한 회수 후 검색에서 사라지기까지 p95 0.42초(문서 제한), 0.40초(그룹 제거). 회수 직후 본문은 40번 모두 바로 막혔습니다.

구현 원칙:

- 위키 접근은 `wiki/source.py`의 인터페이스로 감쌉니다. 파일 위키(`data/wiki/`, 평가·테스트용)와 Spring 위키 API(`wiki/http.py`) 두 구현이 있고, 인덱서는 M3부터, 검색 서버는 M4부터 위키 API를 읽습니다.
- M5 전까지는 OAuth 없이 stdio로만 돕니다. 사용자는 환경 변수(`WIKI_USER=user:alice`)로 정하고, 그룹은 위키에서 읽습니다. 클라이언트 신뢰 등급 기본값은 "외부"입니다.
- 서버 밖 LLM 작업(가상 위키 생성 등)은 코디세이 Public API(`https://copa.codyssey.kr`)의 OpenAI 호환 엔드포인트(`/v1/chat/completions`)와 `gpt-5.4`를 씁니다. 키가 OpenAI 호환용이라 Claude 모델은 이 키로 부를 수 없습니다. 키는 `.env`의 `COPA_API_KEY`에 두고(형식은 `.env.example`), `.env`는 커밋하지 않습니다.

로드맵: M1, M2(골든셋·평가·판정 실험), M3(Spring 위키·아웃박스·증분 인덱싱), M4(권한 pre-filter·CI), M5(HTTP·OAuth·부하·관측성), M6(README·데모) 순서로 진행합니다.

## 결정 원칙

1. 정확성과 보안이 성능보다 먼저입니다. 권한 누출 0건은 타협하지 않습니다.
2. 구성 요소는 명확한 이득이 있을 때만 추가합니다.
3. 측정한 것만 주장합니다. 개선은 골든셋 수치로만 말합니다.
4. 되돌리기 어려운 결정(권한 모델, 이벤트 구조)에 시간을 씁니다.
5. 프레임워크보다 원리를 따릅니다. 핵심 로직은 직접 구현합니다.

## 지켜야 할 제약

- **LangChain·LlamaIndex를 쓰지 않습니다** (ADR-03). 로더·분할기·검색 파이프라인은 직접 구현합니다. 데모 에이전트만 LangGraph를 허용합니다.
- **MCP 서버는 답변을 생성하지 않고 근거만 반환합니다** (ADR-04).
- **도구는 3개, 모두 읽기 전용입니다** (ADR-05, ADR-16): `search_wiki`, `get_document`, `list_recent_changes`. 쓰기 도구는 추가하지 않습니다. 도구에는 `readOnlyHint: true`, `openWorldHint: false`를 붙이지만, 클라이언트가 이 표시를 믿는다고 가정하지 않습니다 (3장).
- **권한은 pre-filter로 적용합니다** (ADR-07). 검색 후에 거르는 방식(post-filter)은 쓰지 않습니다. 필터를 거치지 않은 결과가 로그·캐시·리랭커 입력에 남으면 안 됩니다.
- 문서 리소스 `wiki://doc/{doc_id}`는 `get_document`와 같은 권한 재확인·등급 정책을 거칩니다. 권한·등급 테스트셋은 도구 3개와 리소스에 모두 적용합니다.
- **색인·반환 대상은 위키 원문 문서의 청크입니다** (ADR-18). 여러 문서를 LLM이 종합한 페이지는 권한을 표현할 수 없습니다.
- 인덱싱 이벤트는 신호로만 쓰고 문서 상태는 위키에서 다시 읽습니다. 순서는 내용·권한·등급·삭제마다 오르는 문서 `revision`으로 판단하며, 응답의 `version`(내용 버전)과 다릅니다 (ADR-19).
- 그룹 멤버십은 인덱스에 넣지 않고, 검색할 때 해석합니다 (ADR-08). 그룹 목록을 읽지 못하면 사용자 id만으로 검색하지 않고 오류를 돌려줍니다.
- 문서 권한은 `space_principals`(스페이스 보기 권한)와 `restricted_principals`(문서 제한) 두 필드이고, 둘 다 만족해야 보입니다. 검색 필터는 두 조건을 AND로 묶어 검색 쿼리 안에서 겁니다 (ADR-21, ADR-22).
- 권한 필드가 비어 있으면 아무도 볼 수 없는 문서로 다룹니다. 공개는 `all`로만 표시하고, 제한 없는 문서의 `restricted_principals`도 `all`입니다 (4장 "권한 모델").
- 클라이언트 신뢰 등급은 요청 파라미터로 받지 않고, OAuth 클라이언트 id로 서버가 정합니다 (ADR-17).
- 임베딩 모델은 MCP 서버 안에서 직접 돌리는 `BAAI/bge-m3`이고, float32로 불러옵니다 (ADR-11, 선택 근거는 `experiments/embedding-model/README.md`). BGE-M3의 문장 벡터는 CLS 토큰 벡터입니다. 상용 임베딩 API는 비교 실험에만 씁니다.
- 검색 결과는 "신뢰할 수 없는 외부 콘텐츠"로 표시해 반환합니다 (9장).
- 권한 없는 문서는 흔적을 남기지 않습니다. "N건 제외" 같은 안내도 하지 않습니다.
- 검색 결과 캐시는 만들지 않습니다. 실사용 로그로 반복 질의 비율을 확인할 수 있을 때 다시 검토합니다 (8장). 쿼리 임베딩 캐시는 권한과 무관해 써도 됩니다.

## 응답 형식 (search_wiki)

- 결과 필드: `doc_id`, `title`, `section`, `snippet`, `url`, `version`, `updated_at`, `score`
- 스니펫은 800자, 응답 전체는 약 4천 토큰이 상한입니다.
- 1년 이상 수정되지 않은 문서에는 "오래된 문서" 표시를 붙입니다.

## 청크

- 섹션(제목) 기준으로 나눕니다. 청크당 300~500 토큰, 10~15% 겹침.
- 표와 코드 블록은 자르지 않습니다.
- 앞에 맥락 헤더("스페이스 > 문서 제목 > 섹션 경로")를 붙여 임베딩합니다.
- 섹션 해시를 비교해 바뀐 청크만 다시 임베딩합니다 (ADR-10).

## 목표치

| 지표 | 목표 |
|---|---|
| 무권한 문서 노출 | 0건 |
| Recall@5 (골든셋 중 정답이 있는 270문항) | 0.85 이상 |
| 문서 수정 후 검색 반영까지 p95 | 30초 이하 |
| search_wiki p95 | 800ms 이하 |
| 동시 50요청 오류율 | 1% 미만 |

## 결정 기록 규칙

- 결정 하나당 파일 하나(`docs/adr/ADR-NN.md`)로 기록합니다.
- 원본은 `docs/design.md`의 ADR 절입니다. 설계서를 고친 뒤 `python3 scripts/adr_sync.py`로 파일과 목록을 다시 만들고, ADR 파일을 직접 고치지 않습니다.
- 결정이 바뀌면 기존 파일을 지우지 않습니다. 상태를 `대체됨`으로 바꾸고 새 ADR을 추가합니다.
- "실험으로 검증" 상태인 ADR은 판정 기준이 이미 정해져 있습니다. 결과를 본 뒤 기준을 바꾸지 않습니다. 판정 방법(부트스트랩 신뢰구간, 보류일 때의 선택)은 ADR-20을 따릅니다. M2에서 ADR-02·11·12·13을 모두 판정했습니다.
