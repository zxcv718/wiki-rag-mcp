-- 인가 서버 로그인용 비밀번호 (ADR-24). bcrypt 해시만 저장한다.
-- NULL이면 로그인할 수 없다. 가져오기로 옮긴 사용자는 관리자 API로 비밀번호를 정하기 전까지 로그인하지 못한다.
ALTER TABLE users ADD COLUMN password_hash text;
