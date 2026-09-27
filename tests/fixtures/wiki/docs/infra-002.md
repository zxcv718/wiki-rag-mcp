---
doc_id: infra-002
title: 운영 DB 비밀번호 교체 절차
space: infra
version: 1
revision: 2
updated_at: 2026-08-05T18:00:00+09:00
restricted: [group:dba]
---
# 운영 DB 비밀번호 교체 절차

## 교체 주기

운영 DB 관리자 계정 비밀번호는 90일마다 교체합니다. 교체는 DBA 두 명이 함께 진행합니다.

## 절차

비밀 금고(Vault)에서 새 비밀번호를 발급하고, 애플리케이션 설정을 순차 재시작으로 반영합니다.
