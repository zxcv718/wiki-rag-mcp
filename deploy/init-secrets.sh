#!/usr/bin/env bash
# 서버에서 돈다(push.sh가 배포할 때마다 부른다). deploy/.env와 deploy/secrets/에 없는 비밀만 새로 만들고, 이미 있는
# 값은 바꾸지 않는다. 다시 만들면 DB에 이미 정한 비밀번호, 발급한 토큰과 어긋나기 때문이다. 값은 화면에 찍지 않는다.
set -euo pipefail
cd "$(dirname "$0")"
umask 077

# umask는 새로 만드는 파일에만 걸린다. 손으로 먼저 만든 .env나 폴더가 느슨한 권한이어도 비밀을 쓰기 전에 좁힌다
touch .env
chmod 600 .env
add() { grep -q "^$1=" .env || printf '%s=%s\n' "$1" "$2" >> .env; }
add SEARCH_DB_PASSWORD "$(openssl rand -hex 24)"
add WIKI_DB_PASSWORD "$(openssl rand -hex 24)"
add WIKI_SERVICE_TOKEN "$(openssl rand -hex 32)"
add WIKI_ADMIN_TOKEN "$(openssl rand -hex 32)"
# 시드가 가상 위키 사용자 모두에게 정하는 로그인 비밀번호 (데모용)
add WIKI_DEMO_PASSWORD "$(openssl rand -hex 12)"

# 토큰 서명 키(PKCS#8 PEM). 바뀌면 발급한 토큰이 모두 무효가 된다
mkdir -p secrets
chmod 700 secrets
if [ ! -f secrets/auth-signing-key.pem ]; then
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out secrets/auth-signing-key.pem 2>/dev/null
  # 위키 컨테이너는 root가 아닌 사용자로 돈다. 파일은 읽을 수 있게 두고 폴더(700)로 막는다
  chmod 644 secrets/auth-signing-key.pem
fi
