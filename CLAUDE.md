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
- CI는 `.github/workflows/ci.yml`입니다. Python 테스트와 권한 테스트셋, 골든셋 회귀 검사(`uv run wiki-rag-eval regress`, Recall@5가 `data/golden/baseline.json`보다 2%p 이상 떨어지거나 권한 위반이 1건이면 실패), 위키 서비스 테스트를 돌립니다. 회귀 검사는 임베딩을 `.cache/ci-embeddings.npz`에 캐시합니다. 저장소는 공개 `zxcv718/wiki-rag-mcp`이고, 캐시가 있을 때 CI 전체가 4~5분입니다(임베딩 캐시가 모두 버려지는 PR은 골든셋 작업이 15분쯤).
- 측정(`experiments/m4-permissions`): 권한 회수 후 검색에서 사라지기까지 p95 0.42초(문서 제한), 0.40초(그룹 제거). 회수 직후 본문은 40번 모두 바로 막혔습니다.

**M5 완료**: HTTP 전송과 OAuth, AWS 배포, 관측성, 부하 측정

- 운영 서버는 AWS 서울 EC2 한 대(ADR-23, `infra/`)이고, `https://mcp.dmssh.store/mcp`(검색 서버)와 `https://auth.dmssh.store`(위키 안의 인가 서버, ADR-24)로 열립니다. 배포는 `deploy/push.sh <IP>`로, 서버가 공개 저장소에서 `origin/main`에 있는 커밋을 받아 빌드합니다. 먼저 푸시해야 합니다. 서버의 비밀(`deploy/.env`, `deploy/secrets/`)은 서버에만 있고 값을 출력하지 않습니다.
- HTTP에서는 사용자를 액세스 토큰의 `sub`로, 신뢰 등급을 `client_tier` 클레임으로 정합니다. 인가 서버 계약은 `wiki-service/README.md` "인가 서버"입니다. 우리가 만든 클라이언트는 미리 등록합니다(부하 측정용 `wiki-rag-load`).
- 관측성(ADR-15): 세 서비스가 OTLP로 추적은 Tempo, 지표는 Prometheus에 보내고 Grafana로 봅니다(`deploy/observability/`). 포트는 서버 안에만 열려 있어 SSH 터널로 봅니다. 스팬과 지표에 쿼리 원문과 문서 내용을 넣지 않습니다.
- 부하 측정(`experiments/m5-load`, `experiments/m5-speedup`, ADR-25): 쿼리 임베딩을 묶어 처리하고 배포 서버에서는 쿼리만 bf16으로 인코딩해, 동시 50의 처리량이 초당 3.2건에서 33건, 오류율이 1.44%에서 0%가 됐습니다. 동시 50의 p95는 2.3초로 목표(800ms)를 못 맞췄고, 물리 코어 하나가 임베딩으로 차 있는 것이 원인입니다. 동시 10의 p95는 0.5초입니다.
- 성능 개선도 판정 기준을 측정 전에 적고 하나씩 잽니다. 기준을 못 맞춘 변경은 적용하지 않거나 되돌립니다(PyTorch 스레드 2개는 벤치마크에서 기각, 연결 풀은 배포 뒤 되돌림).
- 알려진 위험은 설계서 10장 "알려진 위험"에 있습니다.

**M6 완료**: 데모 에이전트와 그 측정, 문서 정리

- 데모 에이전트는 `src/wiki_rag_mcp/agent/`입니다. `uv run wiki-rag-demo`가 운영 서버에 브라우저로 로그인해(미리 등록한 공개 클라이언트 `wiki-rag-demo`) 답합니다. LLM은 COPA `gpt-5.4`이고, 도구 호출 반복은 프레임워크 없이 직접 짰습니다.
- 측정은 운영 서버에서 합니다(`experiments/m6-agent`, `uv run wiki-rag-agent-eval`). 토큰은 `token.sh`가 서버 안에서 받아 평가 프로세스의 메모리에만 둡니다. 결과: 도구 선택 통과, 근거 충실도 0.960, 인용 정확도 0.940, 인젝션은 지시문이 도달한 24번 중 따름 0번. 첫 채점은 평가 도구 버그(근거에서 `notes` 누락)로 무효였고, 두 채점을 함께 남겼습니다. 사람 검수 10개는 Claude가 채점 결과를 보지 않고 쓴 초안(`review/claude.yaml`)을 사용자가 확정했고(`review/human.yaml`), 채점 모델과 일치율은 근거 90%, 인용 100%입니다.
- 후속 실험(`experiments/m6-tool-scope`, `python -m wiki_rag_mcp.agent.scope`): 서버 안내문과 `search_wiki` 설명에 범위를 적는 변경은 기각했습니다. 일반 클라이언트도 지금 문구로 회사 질문 24개 중 23개를 찾았고, 넓히면 법령 질문의 과잉 호출이 늘었습니다. COPA는 Claude 모델에 도구 정의를 넘기지 않아 Claude는 잴 수 없습니다.
- 측정에서 에이전트가 `search_wiki`의 `space`에 없는 스페이스 id를 지어내 빈 결과를 받았습니다. 그래서 `space`를 줬는데 결과가 없으면 안내 오류를 돌려줍니다(응답 형식은 그대로).
- 실험 전체 요약은 `experiments/README.md`입니다. 운영 위키의 그룹 멤버십은 `deploy/membership.sh`가 서버 안에서 관리자 API를 불러 바꿉니다(권한 회수를 운영 서버에서 보여 줄 때).

구현 원칙:

- 위키 접근은 `wiki/source.py`의 인터페이스로 감쌉니다. 파일 위키(`data/wiki/`, 평가·테스트용)와 Spring 위키 API(`wiki/http.py`) 두 구현이 있고, 인덱서는 M3부터, 검색 서버는 M4부터 위키 API를 읽습니다.
- 로컬 개발(`.mcp.json`)은 OAuth 없이 stdio로 돕니다. 사용자는 환경 변수(`WIKI_USER=user:alice`)로 정하고, 그룹은 위키에서 읽습니다. 클라이언트 신뢰 등급 기본값은 "외부"입니다. 운영은 HTTP + OAuth입니다(M5).
- 서버 밖 LLM 작업(가상 위키 생성, 데모 에이전트와 그 채점)은 코디세이 Public API(`https://copa.codyssey.kr`)의 OpenAI 호환 엔드포인트(`/v1/chat/completions`)를 씁니다. 생성과 데모 에이전트는 `gpt-5.4`, 에이전트 답변 채점은 다른 계열인 `claude-opus-4-8`입니다. 2026-09-28부터 이 키로 Claude 모델도 응답하며, `claude-opus-4-8`에는 `temperature`를 넘기지 않습니다(502). 키는 `.env`의 `COPA_API_KEY`에 두고(형식은 `.env.example`), `.env`는 커밋하지 않습니다.

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
- 임베딩 모델은 MCP 서버 안에서 직접 돌리는 `BAAI/bge-m3`이고, float32로 불러옵니다 (ADR-11, 선택 근거는 `experiments/embedding-model/README.md`). 배포 서버의 쿼리만 bf16입니다(`WIKI_QUERY_DTYPE`, ADR-25). 문서 벡터는 늘 float32입니다. BGE-M3의 문장 벡터는 CLS 토큰 벡터입니다. 상용 임베딩 API는 비교 실험에만 씁니다.
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

## 커밋 메시지

Conventional Commits 형식에 한국어 제목을 씁니다(예: `fix(deploy): 검색 이미지를 한 번만 빌드`). type과 scope 목록, 본문과 꼬리말 규칙은 `.gitmessage`가 원본이고, 형식은 `.githooks/commit-msg`가 검사합니다. 새로 clone하면 `git config commit.template .gitmessage && git config core.hooksPath .githooks`로 켭니다.

2026-09-28에 그 전 커밋 44개의 메시지를 이 형식으로 고쳤습니다(파일 내용은 그대로). 실험 기록(`experiments/*/runs/*.meta.json`)과 증빙에 적힌 옛 커밋 해시는 태그 `archive/pre-conventional-commits`에서 찾습니다.
