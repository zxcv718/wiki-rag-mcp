"""골든셋 300문항 만들기 (7장 골든셋 구성, ADR-14, ADR-20).

    select   유형별 문항 수에 맞춰 출처(핵심 사실)와 질문자를 시드로 고른다        data/golden/sources.yaml
    write    출처마다 LLM이 질문을 쓴다. 제목은 보여 주지 않고 답을 질문에 넣지 않게 한다  data/golden/questions.jsonl
    blind    출처를 뺀 질문 목록을 내보낸다. 다른 모델이 출처를 모른 채 정답 문서를 찾는다
    merge    출처 라벨과 블라인드 라벨을 합쳐 골든셋을 만든다. 둘이 다르면 검토 목록에 올린다  data/golden/golden.jsonl

    uv run python -m tools.goldset select
    uv run --group wikigen python -m tools.goldset write
"""
