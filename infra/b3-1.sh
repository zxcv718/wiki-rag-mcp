#!/usr/bin/env bash
# 과제 B3-1 평가용 스택을 띄우고 지운다 (docs/cleanup-checklist.md). 운영 스택(default workspace)은 건드리지 않는다.
#
#   infra/b3-1.sh up [--staging]   # 평가 전: 띄우고 접속 주소를 출력한다. --staging은 리허설용 시험 인증서
#   infra/b3-1.sh down             # 평가 끝: 지우고, 남은 자원을 세고, DuckDNS 값을 비운다
#
# DuckDNS 이름과 토큰은 저장소 밖 파일에 둔다:
#   ~/.config/wiki-rag/duckdns.env
#     DUCKDNS_SUBDOMAIN=<이름>
#     DUCKDNS_TOKEN=<토큰>
set -euo pipefail
case "${1:-}" in up|down) ;; *) sed -n '2,5p' "$0"; exit 1 ;; esac
cd "$(dirname "$0")"
export AWS_PROFILE="${AWS_PROFILE:-wiki-rag}"

# shellcheck source=/dev/null
source ~/.config/wiki-rag/duckdns.env
export TF_VAR_duckdns_subdomain="$DUCKDNS_SUBDOMAIN" TF_VAR_duckdns_token="$DUCKDNS_TOKEN"

# workspace를 select로 바꾸면 스크립트가 끝난 뒤에도 b3-1로 남아, 다음에 손으로 친 terraform 명령이 엉뚱한
# 스택에 닿는다. 이 프로세스에만 TF_WORKSPACE로 지정한다
if [ ! -d terraform.tfstate.d/b3-1 ]; then
  terraform workspace new b3-1 >/dev/null
  terraform workspace select default >/dev/null
fi
export TF_WORKSPACE=b3-1

case "${1:-}" in
  up)
    staging=false
    [ "${2:-}" = --staging ] && staging=true
    # SSH는 지금 이 컴퓨터의 IP에서만 연다. 집 IP가 바뀌어도 평가 때마다 맞춰진다
    my_ip="$(curl -sf https://checkip.amazonaws.com)/32"
    echo "SSH 허용 IP: $my_ip, 시험 인증서: $staging"
    start=$(date +%s)
    terraform apply -var "ssh_cidr=$my_ip" -var "acme_staging=$staging"
    echo "인프라 생성 $(( $(date +%s) - start ))초 (승인 입력 포함)"
    SECONDS=0   # 아래 응답 시간은 인프라를 만든 뒤부터 잰다
    ip=$(terraform output -raw public_ip)
    site=$(terraform output -raw site_url)
    echo "서버가 웹 서비스를 띄우기를 기다린다"
    http_ok=false
    for _ in $(seq 120); do
      curl -sf -m 5 "http://$ip/health" >/dev/null && { http_ok=true; break; }
      sleep 5
    done
    $http_ok && echo "http://$ip/health 응답 (생성 뒤 ${SECONDS}초)" || echo "http://$ip 아직 응답 없음. 서버에서 sudo cat /var/log/cloud-init-output.log 확인"
    insecure=""; [ "$staging" = true ] && insecure=-k   # 시험 인증서는 브라우저와 curl이 믿지 않는다
    https_ok=false
    for _ in $(seq 60); do
      curl -sf -m 5 $insecure "$site/health" >/dev/null && { https_ok=true; break; }
      sleep 5
    done
    $https_ok && echo "$site/health 응답 (생성 뒤 ${SECONDS}초)" || echo "$site 아직 응답 없음. 서버에서 sudo cat /var/log/cloud-init-output.log 확인"
    echo
    echo "방식 A: http://$ip"
    echo "방식 B: http://$ip/health"
    echo "HTTPS:  $site"
    terraform output -raw ssh; echo
    ;;
  down)
    start=$(date +%s)
    terraform destroy
    echo "삭제 $(( $(date +%s) - start ))초 (승인 입력 포함)"
    echo
    STACK=b3-1 ./check-leftovers.sh
    # 반납한 IP는 다른 고객에게 갈 수 있다. DuckDNS 값을 남겨 두면 가비아 CNAME을 따라 내 도메인이 남의 서버를 가리킨다
    [ "$(curl -sf "https://www.duckdns.org/update?domains=$DUCKDNS_SUBDOMAIN&token=$DUCKDNS_TOKEN&clear=true" || true)" = OK ] \
      || { echo "DuckDNS 값 비우기 실패. duckdns.org에서 직접 비운다" >&2; exit 1; }
    # 권한 서버에 바로 묻는다. 공용 리졸버는 TTL 동안 옛 IP를 줄 수 있다
    left=$(dig +short "$DUCKDNS_SUBDOMAIN.duckdns.org" @ns1.duckdns.org 2>/dev/null | tail -1 || true)
    echo "DuckDNS $DUCKDNS_SUBDOMAIN.duckdns.org 값 비움 (권한 서버 조회 값: ${left:-없음})"
    ;;
esac
