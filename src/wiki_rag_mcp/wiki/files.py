"""파일로 된 가상 위키 (M1). 디렉터리 구조는 다음과 같다.

    schema.yaml       스페이스(표시 이름, 보기 권한, 기본 등급)와 사용자별 그룹
    docs/*.md         YAML front matter가 붙은 Markdown 문서

front matter의 `restricted`가 없으면 제한 없는 문서(["all"])다. `classification`은 스페이스 기본 등급보다
높일 수만 있다 (ADR-17).
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from wiki_rag_mcp.models import Classification, Document
from wiki_rag_mcp.search.filters import ALL, validate_stored_principals

_REQUIRED = ("doc_id", "title", "space", "version", "revision", "updated_at")
_LEVEL = {c: i for i, c in enumerate(Classification)}


def _split_front_matter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: front matter가 없다")
    _, meta, body = text.split("---\n", 2)
    return yaml.safe_load(meta) or {}, body.lstrip("\n")


class FileWikiSource:
    def __init__(self, root: Path):
        self.root = Path(root)
        schema = yaml.safe_load((self.root / "schema.yaml").read_text(encoding="utf-8"))
        self.base_url = schema.get("wiki_url", "https://wiki.example").rstrip("/")
        self.spaces: dict[str, dict[str, Any]] = schema["spaces"]
        self.users: dict[str, list[str]] = schema.get("users", {})
        self._docs = {d.doc_id: d for d in (self._load(p) for p in sorted((self.root / "docs").glob("*.md")))}

    def _load(self, path: Path) -> Document:
        meta, body = _split_front_matter(path.read_text(encoding="utf-8"), path)
        missing = [k for k in _REQUIRED if k not in meta]
        if missing:
            raise ValueError(f"{path}: front matter에 {missing}가 없다")
        space = self.spaces.get(meta["space"])
        if space is None:
            raise ValueError(f"{path}: 스키마에 없는 스페이스 {meta['space']!r}")
        space_level = Classification(space.get("classification", Classification.GENERAL))
        doc_level = Classification(meta.get("classification", space_level))
        updated_at = meta["updated_at"]
        return Document(
            doc_id=str(meta["doc_id"]),
            title=str(meta["title"]),
            space=str(meta["space"]),
            url=f"{self.base_url}/doc/{meta['doc_id']}",
            version=int(meta["version"]),
            revision=int(meta["revision"]),
            updated_at=updated_at if isinstance(updated_at, datetime) else datetime.fromisoformat(str(updated_at)),
            body=body,
            space_principals=validate_stored_principals(space.get("principals", [])),
            restricted_principals=validate_stored_principals(meta.get("restricted", [ALL])),
            classification=max(space_level, doc_level, key=_LEVEL.__getitem__),
        )

    def documents(self) -> list[Document]:
        return list(self._docs.values())

    def document(self, doc_id: str) -> Document | None:
        return self._docs.get(doc_id)

    def groups_of(self, user_id: str) -> list[str]:
        return [f"group:{g}" for g in self.users.get(user_id, [])]

    def space_title(self, space: str) -> str:
        return self.spaces.get(space, {}).get("title", space)
