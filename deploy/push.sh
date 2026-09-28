#!/usr/bin/env bash
# 푸시된 커밋을 서버에 받아 이미지를 빌드하고 컨테이너를 다시 띄운다.
#
#   deploy/push.sh "$(terraform -chdir=infra output -raw public_ip)" [커밋, 기본 HEAD]
#
# 서버는 공개 저장소를 git으로 받는다. 커밋한 파일만 올라가므로 노트북의 .env나 자격 증명 파일이 섞여 나가지 않는다.
# 서버의 비밀(deploy/.env, deploy/secrets/)은 init-secrets.sh가 처음 한 번 만든다. 도메인(DOMAIN, AUTH_DOMAIN)은
# 처음 한 번 deploy/.env.example을 보고 서버의 deploy/.env에 적는다.
set -euo pipefail

host="${1:?서버 IP를 첫 인자로 주세요}"
cd "$(dirname "$0")"
commit="$(git rev-parse "${2:-HEAD}")"
key="${SSH_KEY:-$HOME/.ssh/wiki-rag-m5.pem}"
ssh_opts=(-i "$key" -o StrictHostKeyChecking=accept-new)

git fetch -q origin
if ! git merge-base --is-ancestor "$commit" origin/main; then
  echo "origin/main에 없는 커밋입니다. 먼저 푸시하세요: $commit" >&2
  exit 1
fi

# 첫 부팅 설정(Docker 설치)이 끝나기 전에 배포하지 않는다
ssh "${ssh_opts[@]}" "ubuntu@$host" 'cloud-init status --wait >/dev/null'
ssh "${ssh_opts[@]}" "ubuntu@$host" bash -s -- "$(git remote get-url origin)" "$commit" <<'EOF'
set -euo pipefail
[ -d ~/wiki-rag-mcp ] || git clone -q "$1" ~/wiki-rag-mcp
cd ~/wiki-rag-mcp
before="$(git rev-parse -q --verify HEAD || true)"
git fetch -q origin
git checkout -q --detach "$2"
deploy/init-secrets.sh
cd deploy
docker compose up -d --build --remove-orphans
# Caddyfile은 파일 하나를 bind mount한다. git은 파일을 새로 만들어 바꾸므로 컨테이너는 옛 파일(inode)을 계속 보고,
# compose는 내용이 바뀐 줄 모른다. 바뀐 배포에서만 재시작해 새 파일을 잡게 한다(1~2초 끊김, docs/troubleshooting.md)
if [ -n "$before" ] && ! git diff --quiet "$before" HEAD -- Caddyfile; then
  docker compose restart caddy
fi
# 새로 빌드해 이름이 넘어간 옛 이미지와, 어느 이미지도 쓰지 않는 빌드 캐시를 지운다. 20GB 디스크에서 배포할 때마다
# 쌓이지 않게 한다. 의존성이 바뀌면 옛 의존성 층이 캐시에 5.6GB 남는다(docs/troubleshooting.md 3번)
docker image prune -f >/dev/null
docker builder prune -f >/dev/null
docker compose ps
EOF
