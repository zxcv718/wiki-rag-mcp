#!/usr/bin/env bash
# 부하 측정 한 벌(동시 1, 10, 50, 100)을 노트북에서 돌린다. 방법과 판정 기준은 README.md에 있다.
#
#   experiments/m5-load/run.sh "$(terraform -chdir=infra output -raw public_ip)"
#
# 서버에 이 폴더가 든 커밋이 배포되어 있어야 한다(tokens.py를 서버의 저장소에서 읽는다). 단계는 STAGES로 바꿀 수 있다.
set -euo pipefail

host="${1:?서버 IP를 첫 인자로 주세요}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
auth="${AUTH:-https://auth.dmssh.store}"
resource="${RESOURCE:-https://mcp.dmssh.store/mcp}"
stages="${STAGES:-1:60 10:120 50:120 100:120}"  # 동시 요청 수:초
key="${SSH_KEY:-$HOME/.ssh/wiki-rag-m5.pem}"
remote=(ssh -i "$key" "ubuntu@$host")
k6_image=grafana/k6:2.3.0
out="$here/runs/$(date +%Y%m%d-%H%M)"
mkdir -p "$out"

# 토큰은 10분짜리 비밀이라 저장소 밖 임시 파일에만 두고, 끝나면 지운다
tokens="$(mktemp)"
trap 'rm -f "$tokens"' EXIT
users="$(python3 -c 'import json, sys; print(" ".join(sorted({json.loads(l)["asker"] for l in open(sys.argv[1])})))' \
  "$repo/data/golden/golden.jsonl")"

docker pull -q "$k6_image" >/dev/null
# 네트워크 기준값: 상태 확인 경로의 연결 시간과 첫 바이트까지의 시간(초)
for _ in 1 2 3 4 5; do
  curl -s -o /dev/null -w "connect %{time_connect} first_byte %{time_starttransfer}\n" "${resource%/mcp}/health"
done > "$out/network.txt"

prom() {  # 서버의 Prometheus에 쿼리한다. 포트가 서버 안에만 열려 있어 SSH로 부른다
  "${remote[@]}" "curl -s -G localhost:9090/api/v1/query --data-urlencode $(printf %q "query=$1") \
    --data-urlencode time=$2"
}

for stage in $stages; do
  vus="${stage%%:*}" seconds="${stage##*:}"
  "${remote[@]}" "cd ~/wiki-rag-mcp/deploy && docker compose --profile tools run --rm -q \
    -v ~/wiki-rag-mcp/experiments/m5-load/tokens.py:/tmp/tokens.py seed python /tmp/tokens.py $auth $resource $users" \
    > "$tokens"
  "${remote[@]}" "vmstat -t 5 $((seconds / 5 + 1))" > "$out/vmstat-$vus.txt" &
  start="$(date +%s)"
  docker run --rm -v "$here/load.js:/load.js:ro" -v "$repo/data/golden/golden.jsonl:/golden.jsonl:ro" \
    -v "$tokens:/tokens.json:ro" -v "$out:/out" -e VUS="$vus" -e DURATION="${seconds}s" -e RESOURCE="$resource" \
    "$k6_image" run --quiet /load.js | tee -a "$out/summary.txt"
  end="$(date +%s)"
  wait
  echo "동시 $vus: 시작 $start 끝 $end" >> "$out/stages.txt"
  # 서버 쪽 단계별 지연과 그룹 캐시 적중률. 앱이 지표를 15초마다 보내므로 마지막 전송을 기다렸다가 단계 구간만큼 본다
  sleep 20
  at=$((end + 15)) window="$((end - start))s"
  {
    for q in 0.5 0.95; do
      prom "histogram_quantile($q, sum by (le, stage) (increase(wiki_search_stage_duration_seconds_bucket[$window])))" $at
      echo
    done
    prom "sum(increase(wiki_group_cache_lookups_total{result=\"hit\"}[$window])) / sum(increase(wiki_group_cache_lookups_total[$window]))" $at
    echo
  } > "$out/server-$vus.json"
  sleep 10  # 다음 단계 전에 줄이 빠지게 둔다(앞의 기다림과 합쳐 30초)
done
echo "결과: $out"
