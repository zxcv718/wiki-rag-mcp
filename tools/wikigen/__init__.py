"""평가용 가상 위키 생성기 (7장 "평가용 데이터").

Karpathy의 LLM 위키 방식에서 스키마·기록·점검만 빌린다. 서비스에는 쓰지 않는다(ADR-18).

    data/wiki/schema.yaml      생성 스키마: 조직도, 사용자, 스페이스와 권한 매트릭스, 약어 사전, 없어야 할 주제
    tools/wikigen/plants.yaml  심을 사례: 개정 전후·모순 쌍, 제한·기밀·빈 권한·인젝션 문서
    data/wiki/manifest.yaml    문서 계획 겸 생성 기록: 문서마다 id, 핵심 사실, 심은 의도
    data/wiki/generation_log.jsonl  LLM 호출 기록: 단계, 대상, 모델, 프롬프트 해시, 토큰, 검증 결과
    data/wiki/docs/*.md        생성한 문서

단계는 plan(문서 계획) → write(본문 생성) → lint(점검)이다.

    uv run --group wikigen python -m tools.wikigen plan
    uv run --group wikigen python -m tools.wikigen write --limit 5
    uv run python -m tools.wikigen lint
"""
