# ADR-15. OpenTelemetry 기반 관측성

- 상태: 확정
- 주요 대안: LangSmith, 로그만 사용
- 출처: 설계서 8. 운영과 성능

| 선택지 | 장점 | 단점 |
|---|---|---|
| LangSmith | LLM 호출 추적과 평가 화면이 강력 | 이 서버는 LLM 생성 호출이 없어 강점을 쓰기 어렵고, Spring·인덱서까지 한 추적으로 묶기 어려움, 유료 SaaS 의존 |
| 로그만 사용 | 가장 단순 | 단계별 지연을 분해할 수 없어 지연 예산을 검증할 수 없음 |
| **OpenTelemetry + Prometheus·Grafana (선택)** | 벤더 중립, 위키·인덱서·MCP 서버를 한 추적으로 연결 | 초기 설정 부담 |

- **결정 이유**: 지연 예산과 인덱싱 지연 같은 핵심 지표가 여러 서비스에 걸쳐 있어, 서비스 경계를 넘는 추적이 필수입니다.
- **수집 지표**: 단계별 검색 지연, 인덱싱 지연, 캐시 적중률, DLQ 적재량, 임베딩 호출 수.
- **구현 (M5)**:
  - 계측은 OpenTelemetry로 하고 OTLP로 보냅니다. 추적은 Tempo가, 지표는 Prometheus의 OTLP 수신 기능이 받습니다. 수집기(Collector)는 두지 않습니다. 받는 곳이 둘뿐이고 중간에 가공할 것이 없기 때문입니다. 세 도구는 배포 compose에 함께 뜹니다(`deploy/observability/`).
  - 검색: MCP Python SDK가 요청마다 `tools/call search_wiki` 스팬을 만들고(MCP 시맨틱 규약), 그 아래에 8장 지연 예산 표의 네 단계를 `principals`(권한 해석), `embed_query`, `vector_search`, `assemble` 스팬으로 남깁니다. 위키 API 호출에는 httpx 계측이 `traceparent` 헤더를 붙여, 그룹 조회와 본문 재확인이 위키의 처리 스팬까지 한 추적으로 이어집니다.
  - 인덱싱: 위키는 변경을 일으킨 요청의 `traceparent`를 아웃박스 행에 적고, 이벤트에 실어 보냅니다(`wiki-service/README.md` "이벤트"). 워커는 이 값을 부모로 이벤트마다 처리 스팬을 엽니다. OpenTelemetry 메시징 규약은 링크를 기본으로 권하지만, 메시지 하나를 다른 스팬 밖에서 처리할 때는 만든 쪽을 부모로 써도 된다고 둡니다. 그래서 문서 편집부터 검색 반영까지가 한 추적에 보입니다.
  - 지표 이름: `wiki.search.stage.duration`(단계별 검색 시간), `wiki.indexing.lag`(아웃박스 기록부터 반영까지, 멤버십 이벤트는 그룹 캐시 무효화까지), `wiki.group_cache.lookups`(적중, 못 찾음, Redis 오류), `wiki.indexing.queue.length`(문서·멤버십 스트림과 DLQ에 남은 이벤트), `wiki.embedding.texts`(쿼리와 청크로 나눈 임베딩 수). 위키는 Spring Boot가 내는 HTTP·JVM 지표를 그대로 씁니다.
  - 표본은 모두 남깁니다. Spring Boot의 기본값(10%)을 1.0으로 바꿨습니다. 요청이 적은 데모 서버이고, 부하 측정도 같은 조건에서 잽니다. 위키의 스케줄 작업(0.5초마다 도는 아웃박스 폴러), 보안 필터 체인, Redis 명령은 관측에서 뺍니다. 위키는 추적 맥락을 W3C 형식으로만 받고, Caddy는 인터넷에서 인가 서버로 온 추적 헤더를 지웁니다. 밖의 클라이언트가 "표본에서 빼라"는 표시로 로그인 시도 같은 요청을 추적에서 빼지 못하게 하기 위해서입니다. MCP 클라이언트가 `_meta`로 보낸 추적 맥락은 규약대로 이어 주되, 검색 서버도 "빼라"는 표시는 따르지 않습니다. 폴러가 추적을 하나씩 만들어 쌓이고, 보안 필터와 Redis 명령은 요청 하나에 스팬 수십 개를 붙이거나 부모 없는 추적이 되기 때문입니다.
  - 스팬과 지표에 쿼리 원문과 문서 내용은 넣지 않습니다(9장 "감사 로그와 호출 제한", ADR-07). 위키 요청 주소에 든 사용자 id와 문서 id는 추적에 남으므로, 추적은 3일만 두고 Grafana와 두 저장소의 포트는 서버 안(127.0.0.1)에만 엽니다. 운영자는 SSH 터널로 봅니다.
- **감수한 비용**: 서버 메모리 8GB를 임베딩 모델 두 벌과 나눠 써서, 세 도구에 메모리 상한(Prometheus 512MB, Tempo 512MB, Grafana 384MB)을 둡니다. 상한을 넘으면 그 컨테이너만 종료되고 다시 뜹니다. 서버 전체의 메모리가 모자라 검색 서버가 먼저 종료되는 일을 막기 위해서입니다. 대시보드를 인터넷에 공개하지 않아, 결과는 README의 캡처로 보여 줍니다. Prometheus는 OTLP로 받은 지표의 시작 시각을 쓰지 않아, 새로 생긴 시계열의 첫 값은 `rate`와 `increase`에서 빠집니다. 카운터는 프로세스가 뜰 때 0으로 만들어 두고, 히스토그램은 요청이 드문 때 재시작 직후의 첫 관측이 그래프에 늦게 보입니다. 추적에는 모두 남습니다.
- **참고**: [OpenTelemetry: 컨텍스트 전파](https://opentelemetry.io/docs/concepts/context-propagation/) (서비스와 프로세스 사이로 추적 정보를 넘기는 방식), [W3C Trace Context](https://www.w3.org/TR/trace-context/) (전파에 쓰는 표준 HTTP 헤더). 위키(Spring)와 인덱서·MCP 서버(Python)를 한 추적으로 잇는 근거입니다. [OpenTelemetry 메시징 스팬 규약](https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/) (처리 스팬의 부모와 링크), [OpenTelemetry MCP 시맨틱 규약](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/mcp.md) (`{mcp.method.name} {target}` 스팬 이름), [Prometheus OTLP 수신](https://prometheus.io/docs/guides/opentelemetry/)
