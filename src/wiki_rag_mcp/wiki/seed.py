"""`wiki-rag-seed`: 파일로 된 가상 위키(data/wiki)를 Spring 위키 서비스로 옮긴다 (POST /admin/import).

doc_id, version, revision, updated_at을 그대로 옮겨 골든셋과 평가 결과가 계속 맞게 한다. 위키가 문서마다
이벤트를 내므로, 워커가 떠 있으면 옮기는 것만으로 색인까지 된다.

WIKI_DEMO_PASSWORD가 있으면 옮긴 사용자 모두에게 그 비밀번호를 정해(PUT /admin/users/{user_id}/password),
인가 서버 로그인(M5, ADR-24)을 바로 해 볼 수 있게 한다. 비밀번호가 없는 사용자는 로그인할 수 없다.
"""

import argparse
from pathlib import Path
from typing import Any

import httpx
import yaml

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.models import Classification
from wiki_rag_mcp.wiki.files import FileWikiSource

MIN_PASSWORD_CHARS = 12  # 위키 관리자 API의 하한 (wiki-service/README.md "로그인")


def import_payload(wiki_dir: Path) -> dict[str, Any]:
    schema = yaml.safe_load((wiki_dir / "schema.yaml").read_text(encoding="utf-8"))
    source = FileWikiSource(wiki_dir)
    spaces = schema["spaces"]
    documents = []
    for doc in source.documents():
        default = Classification(spaces[doc.space].get("classification", Classification.GENERAL))
        documents.append({
            "doc_id": doc.doc_id, "space": doc.space, "title": doc.title, "body": doc.body,
            "version": doc.version, "revision": doc.revision, "updated_at": doc.updated_at.isoformat(),
            "restricted_principals": list(doc.restricted_principals),
            # 스페이스 기본값과 같으면 문서 등급을 따로 두지 않는다. 그래야 나중에 스페이스 등급이 바뀌면 따라간다
            "classification": None if doc.classification == default else str(doc.classification),
        })
    # 조직도에 없어도 사용자 소속이나 권한 목록에 나오는 그룹은 모두 만든다. 위키에 그룹이 있어야 멤버십을
    # 만들고, 나중에 관리자 API로 멤버를 넣을 수 있다
    names = {o["group"]: o["team"] for o in schema.get("org", [])}
    referenced = {g for gs in schema["users"].values() for g in gs}
    referenced |= {p.removeprefix("group:") for s in spaces.values() for p in s.get("principals", [])
                   if p.startswith("group:")}
    referenced |= {p.removeprefix("group:") for d in documents for p in d["restricted_principals"]
                   if p.startswith("group:")}
    return {
        "spaces": [{"space": name, "title": s["title"], "principals": list(s.get("principals", [])),
                    "classification": s.get("classification", "general")} for name, s in spaces.items()],
        "groups": [{"group": g, "name": names.get(g, g)} for g in sorted(names.keys() | referenced)],
        "users": [{"user_id": u, "name": u, "groups": list(gs)} for u, gs in schema["users"].items()],
        "documents": documents,
    }


def seed(wiki: httpx.Client, payload: dict[str, Any], password: str | None) -> None:
    """관리자 API로 옮긴다. wiki는 위키 주소와 관리자 토큰을 붙인 클라이언트다."""
    # 옮기기 전에 확인한다. 옮긴 뒤에 실패하면 빈 위키가 아니게 되어 다시 옮길 수 없다
    if password is not None and len(password) < MIN_PASSWORD_CHARS:
        raise SystemExit(f"WIKI_DEMO_PASSWORD는 {MIN_PASSWORD_CHARS}자 이상이어야 합니다.")
    response = wiki.post("/admin/import", json=payload)
    if response.status_code == 409:
        raise SystemExit("위키에 이미 문서가 있어 옮기지 않았습니다.")
    response.raise_for_status()
    print(f"옮김: 스페이스 {len(payload['spaces'])}개, 그룹 {len(payload['groups'])}개, "
          f"사용자 {len(payload['users'])}개, 문서 {len(payload['documents'])}개")
    if password is not None:
        for user in payload["users"]:
            wiki.put(f"/admin/users/{user['user_id']}/password", json={"password": password}).raise_for_status()
        print(f"비밀번호를 정함: 사용자 {len(payload['users'])}명")


def main(argv: list[str] | None = None) -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="가상 위키 파일을 Spring 위키 서비스로 옮긴다.")
    parser.add_argument("--wiki-dir", type=Path, default=settings.wiki_dir)
    args = parser.parse_args(argv)

    with httpx.Client(base_url=settings.wiki_api_url.rstrip("/"), timeout=60,
                      headers={"Authorization": f"Bearer {settings.wiki_admin_token}"}) as wiki:
        seed(wiki, import_payload(args.wiki_dir), settings.demo_password)


if __name__ == "__main__":
    main()
