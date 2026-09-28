# ADR-04. MCP 서버는 근거만 반환

- 상태: 확정
- 주요 대안: 서버에서 답변까지 생성
- 출처: 설계서 3. MCP 인터페이스 설계

| 선택지 | 장점 | 단점 |
|---|---|---|
| 서버가 답변까지 생성 | 서버 단독으로 완결된 데모 | 클라이언트 LLM과 생성이 중복되어 비용·지연 두 배, 서버에 LLM 키와 프롬프트 관리 부담 |
| **근거만 반환 (선택)** | 생성은 클라이언트가, 서버는 검색 품질에만 집중 | 답변 품질이 클라이언트 모델에 좌우 |

- **결정 이유**: MCP 클라이언트는 이미 LLM이므로 생성을 중복할 이유가 없습니다. 서버의 책임이 "무엇을 근거로 줬는가"로 좁혀져, 품질도 검색 지표만으로 평가할 수 있습니다.
- **감수한 비용**: 서버 단독 시연이 밋밋해, 데모 에이전트로 보완합니다(M6, `wiki-rag-demo`, `experiments/m6-agent`).
- **재검토 조건**: 슬랙 봇처럼 MCP를 쓰지 않는 채널을 지원해야 하면, 답변 생성 API를 별도로 추가합니다.
- **참고**: [Amazon Bedrock Retrieve API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Retrieve.html)는 근거만 돌려주고, [RetrieveAndGenerate API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_RetrieveAndGenerate.html)는 답변까지 만듭니다. 이 설계는 앞쪽만 제공합니다.
