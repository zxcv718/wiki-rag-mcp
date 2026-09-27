# ADR-07. 권한 pre-filter + 본문 조회 시 재확인

- 상태: 확정
- 주요 대안: post-filter, 그룹별 인덱스 분리
- 출처: 설계서 4. 권한 반영 검색

| 선택지 | 장점 | 단점 |
|---|---|---|
| 검색 후 거르기 (post-filter) | 구현이 가장 단순 | 상위 5개를 먼저 뽑고 거르면 제한 문서가 많은 사용자는 결과가 0~1개만 남음. 거르기 전 결과가 로그·캐시·리랭커 입력에 남을 수 있음 |
| 그룹별 인덱스 분리 | 물리적으로 격리 | 여러 그룹이 보는 문서가 중복 저장, 권한 변경 시 대량 재색인 |
| 검색 후 위키 API로 건별 확인 | 항상 최신 권한 | 후보 30개마다 API 호출로 지연 급증, 결과 부족 문제는 post-filter와 동일 |
| **pre-filter + 본문 재확인 (선택)** | 결과 수 보장, 중간 단계 누출 차단 | 인덱스의 권한이 원본과 잠시 어긋날 수 있음 |

- **남는 위험 (명시적 인정)**: 권한이 회수된 직후, 인덱스 반영 전까지 짧은 시간 동안 **스니펫**이 노출될 수 있습니다. 본문은 `get_document`의 재확인으로 차단되고, 이 틈은 ACL_CHANGED 이벤트로 줄여 목표 p95 30초 이내로 관리합니다.
- **재검토 조건**: 권한 변경의 즉시 반영이 규정상 요구되는 환경이라면, 검색 결과의 스니펫도 위키 API로 재확인하고 늘어나는 지연을 감수합니다.
- **참고**: [azure-search-openai-demo](https://github.com/Azure-Samples/azure-search-openai-demo/blob/main/docs/login_and_acl.md)와 [Amazon Bedrock Knowledge Base](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html)는 검색 엔진 안에서 거릅니다. [Azure AI Search 벡터 필터](https://learn.microsoft.com/en-us/azure/search/vector-search-filters)는 postFilter가 결과를 놓칠 수 있다고 설명하고, [OWASP LLM08](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)은 권한을 반영하는 벡터 저장소와 데이터셋의 분리를 권고하는데, 이 설계는 인덱스를 나누지 않고 권한 필터로 분리합니다. 반대 사례로 [Onyx](https://github.com/onyx-dot-app/onyx/blob/main/backend/ee/onyx/external_permissions/post_query_censoring.py)는 일부 소스를 검색이 끝난 뒤에 거릅니다.
