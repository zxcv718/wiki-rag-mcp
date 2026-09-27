---
doc_id: infra-023
title: "운영 DB 백업 복구 훈련"
space: infra
version: 3
revision: 6
updated_at: 2026-06-29T10:36:00+09:00
restricted: ["group:dba"]
---

# 운영 DB 백업 복구 훈련

## 언제 쓰는가
이 문서는 운영 DB 백업으로 실제 복구가 가능한지 점검하는 정기 훈련에 사용합니다. 복구 훈련은 분기마다 한 번 진행합니다. 목적은 백업 파일의 유효성, 복구 절차의 재현 가능성, 담당자 간 역할 분담이 정상적으로 동작하는지 확인하는 것입니다.

훈련은 운영 DB와 분리된 복구용 서버 또는 임시 환경에서만 진행합니다. 운영 DB에 직접 복구하지 않습니다. 목표 복구 시간은 1시간입니다. 시작 시각과 종료 시각은 반드시 기록합니다.

참여 대상은 플랫폼·SRE팀 진행자 1명 이상, 해당 DB를 사용하는 개발본부 담당자 1명 이상입니다. 필요 시 DB 관리자와 데이터팀에 사전 공유합니다.

## 사전 확인
훈련 시작 전에 아래 항목을 확인합니다.

| 항목 | 확인 내용 |
|---|---|
| 대상 선정 | 이번 분기 복구 대상 DB와 백업 시점을 정합니다 |
| 환경 분리 | 복구 대상 서버가 운영 트래픽과 분리되어 있는지 확인합니다 |
| 접근 권한 | 진행자와 확인자가 서버, 백업 저장소, DB 접속 권한이 있는지 확인합니다 |
| 용량 확인 | 복구 서버의 디스크 여유 공간이 충분한지 확인합니다 |
| 백업 무결성 | 백업 파일 존재 여부와 파일 크기, 체크섬 파일 존재 여부를 확인합니다 |
| 공지 | 플랫폼·SRE팀과 관련 개발본부에 훈련 시작 시각을 사전 공유합니다 |

훈련 중 변경 작업이 겹치면 일정을 다시 잡습니다. 고객지원팀 문의 대응에 영향을 줄 수 있는 점검은 포함하지 않습니다.

## 단계별 명령
아래 예시는 PostgreSQL 기준입니다. 경로와 DB 이름은 실제 대상에 맞게 바꿉니다.

1. 복구 작업용 디렉터리를 준비합니다.

```bash
export RESTORE_DATE=2026-06-29
export BACKUP_FILE=/backup/prod/appdb-${RESTORE_DATE}.dump
export CHECKSUM_FILE=${BACKUP_FILE}.sha256
export RESTORE_DB=appdb_restore
export RESTORE_USER=restore_user

mkdir -p /restore/work
cd /restore/work
```

2. 백업 파일과 체크섬을 확인합니다.

```bash
ls -lh ${BACKUP_FILE} ${CHECKSUM_FILE}
sha256sum -c ${CHECKSUM_FILE}
```

3. 복구 대상 DB를 생성합니다.

```bash
psql -h <호스트> -U postgres -d postgres -c "DROP DATABASE IF EXISTS ${RESTORE_DB};"
psql -h <호스트> -U postgres -d postgres -c "CREATE DATABASE ${RESTORE_DB} OWNER ${RESTORE_USER};"
```

4. 백업을 복구합니다.

```bash
time pg_restore \
  -h <호스트> \
  -U ${RESTORE_USER} \
  -d ${RESTORE_DB} \
  --no-owner \
  --no-privileges \
  ${BACKUP_FILE}
```

5. 기본 점검 쿼리를 실행합니다.

```bash
psql -h <호스트> -U ${RESTORE_USER} -d ${RESTORE_DB} -c "\dt"
psql -h <호스트> -U ${RESTORE_USER} -d ${RESTORE_DB} -c "SELECT now();"
psql -h <호스트> -U ${RESTORE_USER} -d ${RESTORE_DB} -c "SELECT count(*) FROM information_schema.tables;"
```

6. 애플리케이션 관점의 최소 확인을 진행합니다. 개발본부 담당자는 주요 테이블 조회, 최근 데이터 존재 여부, 필수 인덱스 생성 여부를 확인합니다.

## 결과 확인
아래 기준을 모두 만족하면 훈련 성공으로 기록합니다.

- 백업 파일 체크섬 검증이 성공합니다.
- 복구 절차가 중단 없이 완료됩니다.
- 전체 소요 시간이 1시간 이내입니다.
- 주요 테이블과 데이터가 조회됩니다.
- 개발본부 담당자가 최소 기능 확인 결과 이상 없음을 회신합니다.

실패 또는 지연이 있으면 원인을 분류해 남깁니다. 예시는 저장소 접근 지연, 디스크 공간 부족, 권한 누락, 복구 명령 오류입니다. 훈련 결과는 PIR 형식으로 남깁니다. PIR은 장애 사후 검토 회의와 그 문서를 뜻합니다. 문서에는 대상 DB, 백업 시점, 실제 소요 시간, 확인자, 문제점, 다음 분기 개선 항목을 포함합니다.

## 되돌리기 및 에스컬레이션
훈련 종료 후 복구용 DB와 임시 파일을 정리합니다.

```bash
psql -h <호스트> -U postgres -d postgres -c "DROP DATABASE IF EXISTS ${RESTORE_DB};"
rm -rf /restore/work/*
```

아래 경우에는 즉시 에스컬레이션합니다.

- 백업 파일이 없거나 체크섬 검증에 실패한 경우
- 복구 중 권한 문제로 진행이 멈춘 경우
- 복구 서버 자원이 부족한 경우
- 복구 결과가 원본 기대값과 크게 다른 경우

에스컬레이션 순서는 플랫폼·SRE팀 내 담당자 공유 후 DB 관리자 확인, 필요 시 해당 개발본부 담당자와 데이터팀 참여 요청 순서로 진행합니다. 운영 DB 자체 이상이 의심되면 정보보안팀이 아니라 DB 관리자에게 먼저 알립니다. 훈련 일정 변경이나 반복 수행이 필요하면 제품기획팀에는 결과만 공유하고 승인 대기 없이 재조정합니다.
