#!/usr/bin/env bash
# 가상 사용자 한 명의 액세스 토큰(10분)을 JSON({사용자: 토큰})으로 출력한다. 평가 스크립트(wiki-rag-agent-eval)가
# 사용자를 바꿀 때와 토큰이 오래됐을 때 부른다. 출력을 파일이나 화면에 남기지 않는다.
#
#   WIKI_HOST="$(terraform -chdir=infra output -raw public_ip)" experiments/m6-agent/token.sh jiho
#
# 토큰은 부하 측정과 같은 방법으로 받는다(experiments/m5-load/tokens.py). 서버의 시드 컨테이너 안에서 데모
# 비밀번호로 로그인과 동의를 거치므로 비밀번호가 서버 밖으로 나오지 않는다. 클라이언트는 데모 에이전트가 쓰는
# 미리 등록한 공개 클라이언트 wiki-rag-demo다(ADR-24).
set -euo pipefail

user="${1:?사용자 id를 첫 인자로 주세요}"
host="${WIKI_HOST:?서버 IP를 WIKI_HOST로 주세요}"
client="${CLIENT_ID:-wiki-rag-demo}"
auth="${AUTH:-https://auth.dmssh.store}"
resource="${RESOURCE:-https://mcp.dmssh.store/mcp}"
key="${SSH_KEY:-$HOME/.ssh/wiki-rag-m5.pem}"

ssh -i "$key" -o BatchMode=yes "ubuntu@$host" "cd ~/wiki-rag-mcp/deploy && docker compose --profile tools run --rm -q \
  -e LOAD_CLIENT_ID=$client -v ~/wiki-rag-mcp/experiments/m5-load/tokens.py:/tmp/tokens.py \
  seed python /tmp/tokens.py $auth $resource $user"
