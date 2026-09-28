#!/usr/bin/env bash
# 배포 설정(deploy/)을 서버에 올리고 컨테이너를 다시 띄운다.
#
#   deploy/push.sh "$(terraform -chdir=infra output -raw public_ip)"
#
# 서버의 deploy/.env(DOMAIN 등)는 덮어쓰지 않는다. 처음 한 번 deploy/.env.example을 보고 서버에서 만든다.
set -euo pipefail

host="${1:?서버 IP를 첫 인자로 주세요}"
key="${SSH_KEY:-$HOME/.ssh/wiki-rag-m5.pem}"
ssh_opts=(-i "$key" -o StrictHostKeyChecking=accept-new)
cd "$(dirname "$0")"

# 첫 부팅 설정(Docker 설치)이 끝나기 전에 배포하지 않는다
ssh "${ssh_opts[@]}" "ubuntu@$host" 'cloud-init status --wait >/dev/null'
rsync -az --delete --exclude .env -e "ssh ${ssh_opts[*]}" ./ "ubuntu@$host:~/deploy/"
ssh "${ssh_opts[@]}" "ubuntu@$host" 'cd ~/deploy && docker compose up -d --remove-orphans && docker compose ps'
