#!/usr/bin/env bash
# 운영 위키에서 사용자를 그룹에 넣거나 뺀다. 데모 영상의 "권한 회수" 장면에 쓴다(docs/demo-script.md).
#
#   deploy/membership.sh "$(terraform -chdir=infra output -raw public_ip)" remove dba taeyang
#   deploy/membership.sh "$(terraform -chdir=infra output -raw public_ip)" add dba taeyang
#
# 관리자 토큰은 서버의 시드 컨테이너 안에서만 쓰고 밖으로 꺼내지 않는다. 관리자 API 계약은 wiki-service/README.md다.
# 위키가 MEMBERSHIP_CHANGED 이벤트를 내보내면 워커가 그 사용자의 그룹 캐시를 무효화한다(ADR-08).
set -euo pipefail

host="${1:?서버 IP를 첫 인자로 주세요}"
action="${2:?add 또는 remove}"
group="${3:?그룹 id}"
user="${4:?사용자 id}"
key="${SSH_KEY:-$HOME/.ssh/wiki-rag-m5.pem}"

case "$action" in
  add) method=PUT ;;
  remove) method=DELETE ;;
  *) echo "두 번째 인자는 add 또는 remove입니다" >&2; exit 1 ;;
esac
# 원격 셸 명령에 그대로 들어가므로 id 형식을 먼저 확인한다
for id in "$group" "$user"; do
  [[ "$id" =~ ^[a-z0-9_-]+$ ]] || { echo "잘못된 id: $id" >&2; exit 1; }
done

ssh -i "$key" -o BatchMode=yes "ubuntu@$host" "cd ~/wiki-rag-mcp/deploy && docker compose --profile tools run --rm -q \
  -e METHOD=$method -e TARGET=/admin/groups/$group/members/$user seed python -c '
import os, sys, httpx
r = httpx.request(os.environ[\"METHOD\"], os.environ[\"WIKI_API_URL\"] + os.environ[\"TARGET\"],
                  headers={\"Authorization\": \"Bearer \" + os.environ[\"WIKI_ADMIN_TOKEN\"]}, timeout=10)
print(os.environ[\"METHOD\"], os.environ[\"TARGET\"], r.status_code)
sys.exit(0 if r.is_success else 1)
'"
