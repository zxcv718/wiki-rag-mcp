# ADR-06. 개발은 stdio, 운영은 HTTP + OAuth

- 상태: 확정
- 주요 대안: 공용 API 키 인증
- 출처: 설계서 3. MCP 인터페이스 설계

- **선택지**: 공용 API 키 인증, OAuth 기반 사용자 인증(선택).
- **결정 이유**: 권한 필터(ADR-07)는 "요청한 사람이 누구인가"를 전제로 합니다. 공용 API 키로는 사용자를 구분할 수 없어 권한 설계 전체가 무의미해집니다. 로컬 개발은 설정이 필요 없는 stdio로 빠르게 반복합니다.
- **추가 결정**: 클라이언트 토큰을 위키 서비스에 그대로 넘기지 않습니다. 토큰이 원래 대상이 아닌 서비스에서 재사용되는 위험을 막기 위해, 서버 간 호출은 별도 서비스 자격 증명을 씁니다.
- **감수한 비용**: OAuth 연동 구현과 테스트 부담.
- **참고**: [MCP 명세: 권한 부여](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) (HTTP 전송은 OAuth 2.1, stdio는 실행 환경의 자격 증명), [MCP 보안 모범 사례](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices) (클라이언트 토큰을 그대로 넘기지 않음)
