"""`wiki-rag-seed`: 파일로 된 가상 위키(data/wiki)를 Spring 위키 서비스로 옮긴다 (POST /admin/import).

doc_id, version, revision, updated_at을 그대로 옮겨 골든셋과 평가 결과가 계속 맞게 한다. 위키가 문서마다
이벤트를 내므로, 워커가 떠 있으면 옮기는 것만으로 색인까지 된다.
"""

import argparse
from pathlib import Path
from typing import Any

import httpx
import yaml

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.models import Classification
from wiki_rag_mcp.wiki.files import FileWikiSource


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


def main(argv: list[str] | None = None) -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="가상 위키 파일을 Spring 위키 서비스로 옮긴다.")
    parser.add_argument("--wiki-dir", type=Path, default=settings.wiki_dir)
    args = parser.parse_args(argv)

    payload = import_payload(args.wiki_dir)
    response = httpx.post(f"{settings.wiki_api_url.rstrip('/')}/admin/import", json=payload, timeout=60,
                          headers={"Authorization": f"Bearer {settings.wiki_admin_token}"})
    if response.status_code == 409:
        raise SystemExit("위키에 이미 문서가 있어 옮기지 않았습니다.")
    response.raise_for_status()
    print(f"옮김: 스페이스 {len(payload['spaces'])}개, 그룹 {len(payload['groups'])}개, "
          f"사용자 {len(payload['users'])}개, 문서 {len(payload['documents'])}개")


if __name__ == "__main__":
    main()
