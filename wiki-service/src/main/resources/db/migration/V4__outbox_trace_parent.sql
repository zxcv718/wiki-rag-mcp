-- 변경을 일으킨 요청의 추적 맥락(W3C traceparent). 인덱서가 이벤트 처리 스팬을 같은 추적에 잇는다 (ADR-15).
-- 추적 중이 아니었던 변경과 이 열을 넣기 전의 행은 NULL이다.
ALTER TABLE outbox ADD COLUMN trace_parent text;
