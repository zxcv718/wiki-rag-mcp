# ADR-10. 섹션 단위 청크 + 해시 기반 증분 반영

- 상태: 확정
- 주요 대안: 문서 전체 재색인, 고정 길이 분할
- 출처: 설계서 5. 인덱싱 파이프라인과 최신성

| 선택지 | 장점 | 단점 |
|---|---|---|
| 문서 전체 재색인 | 가장 단순 | 오타 하나에도 문서 전체 재임베딩 |
| 고정 길이 분할 + 해시 | 구현 쉬움 | 앞부분을 고치면 분할 경계가 밀려 뒤쪽 청크 해시가 모두 바뀌어 증분 효과가 사라짐 |
| **섹션(제목) 기반 분할 + 해시 (선택)** | 수정된 섹션의 청크만 변경 | 섹션 길이 편차가 커서 긴 섹션은 하위 분할 필요 |

- **결정 이유**: 위키 문서는 제목 구조가 뚜렷하고 수정은 대개 한 섹션 안에서 일어납니다. 분할 경계를 문서 구조에 고정해야 해시 비교가 의미를 가집니다.
- **검증 방법**: 수정 시나리오별로 증분 방식과 전체 재색인의 임베딩 호출 수를 비교해 절감률을 기록합니다.
- **참고**: [Azure AI Search: 청크 나누기](https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-chunk-documents) (Markdown·HTML 제목으로 섹션 단위로 나누는 방법, 10~15% 겹침 예), [LlamaIndex ingestion pipeline](https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/) (문서 해시가 바뀐 문서만 다시 처리. 이 설계는 섹션 단위로 해시를 비교)
