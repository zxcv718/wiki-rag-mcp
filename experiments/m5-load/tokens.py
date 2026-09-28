"""부하 측정용 액세스 토큰을 사용자마다 하나씩 받아 JSON({사용자: 토큰})으로 출력한다 (README.md "방법").

운영 서버의 시드 컨테이너 안에서 run.sh가 부른다. 데모 비밀번호는 그 컨테이너의 WIKI_DEMO_PASSWORD에서 읽고 찍지
않는다. 클라이언트는 미리 등록한 공개 클라이언트 wiki-rag-load다(ADR-24). 인가 코드와 PKCE로 로그인과 동의를
거치고, 돌아갈 주소(루프백)로는 실제로 가지 않고 Location 헤더에서 코드만 읽는다.

    python tokens.py <인가 서버> <MCP 리소스> <사용자> [사용자 ...] > tokens.json
"""

import base64
import hashlib
import html
import json
import os
import re
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

CLIENT_ID = os.environ.get("LOAD_CLIENT_ID", "wiki-rag-load")
REDIRECT = "http://127.0.0.1/callback"


def first_form(page: str) -> tuple[str, dict[str, str]]:
    """첫 폼(로그인 화면은 로그인, 동의 화면은 허용)의 주소와 숨은 입력."""
    form = re.search(r"<form[^>]*action=\"([^\"]*)\"[^>]*>(.*?)</form>", page, re.S)
    if form is None:
        raise SystemExit("폼이 없는 화면이 왔다. 인가 서버 설정을 확인하세요")
    fields = {}
    for tag in re.findall(r"<input[^>]*type=\"hidden\"[^>]*>", form.group(2)):
        name, value = re.search(r'name="([^"]*)"', tag), re.search(r'value="([^"]*)"', tag)
        if name:
            fields[html.unescape(name.group(1))] = html.unescape(value.group(1)) if value else ""
    return html.unescape(form.group(1)), fields


def access_token(auth: str, resource: str, user: str, password: str) -> str:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(8)
    query = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT, "scope": "wiki:read",
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "resource": resource}
    with httpx.Client(base_url=auth, follow_redirects=False, timeout=10) as http:
        r = http.get("/oauth2/authorize?" + urlencode(query))
        r = http.get(urljoin(auth, r.headers["location"]))  # 로그인 화면
        action, fields = first_form(r.text)
        r = http.post(urljoin(auth, action), data=fields | {"username": user, "password": password})
        if "error" in urlparse(r.headers.get("location", "")).query:
            raise SystemExit(f"{user}: 로그인에 실패했다. 이 사용자의 비밀번호가 WIKI_DEMO_PASSWORD인지 확인하세요")
        while r.status_code == 302 and not r.headers["location"].startswith(REDIRECT):
            r = http.get(urljoin(auth, r.headers["location"]))
        if r.status_code == 200:  # 공개 클라이언트는 매번 동의 화면이 나온다 (ADR-24)
            action, fields = first_form(r.text)
            r = http.post(urljoin(auth, action), data=fields)
        location = r.headers.get("location", "")
        params = parse_qs(urlparse(location).query)
        if not location.startswith(REDIRECT) or params.get("state") != [state] or "code" not in params:
            raise SystemExit(f"{user}: 인가 코드를 받지 못했다 (상태 {r.status_code})")
        r = http.post("/oauth2/token", data={"grant_type": "authorization_code", "code": params["code"][0],
                                             "redirect_uri": REDIRECT, "client_id": CLIENT_ID,
                                             "code_verifier": verifier, "resource": resource})
        if r.status_code != 200:
            raise SystemExit(f"{user}: 토큰을 받지 못했다 (상태 {r.status_code})")
        return r.json()["access_token"]


def main() -> None:
    auth, resource, *users = sys.argv[1:]
    password = os.environ["WIKI_DEMO_PASSWORD"]
    json.dump({user: access_token(auth, resource, user, password) for user in users}, sys.stdout)


if __name__ == "__main__":
    main()
