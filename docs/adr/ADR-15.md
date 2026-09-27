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
- **참고**: [OpenTelemetry: 컨텍스트 전파](https://opentelemetry.io/docs/concepts/context-propagation/) (서비스와 프로세스 사이로 추적 정보를 넘기는 방식), [W3C Trace Context](https://www.w3.org/TR/trace-context/) (전파에 쓰는 표준 HTTP 헤더). 위키(Spring)와 인덱서·MCP 서버(Python)를 한 추적으로 잇는 근거입니다.
