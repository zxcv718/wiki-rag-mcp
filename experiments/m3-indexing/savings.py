"""증분 색인의 임베딩 절감률 (ADR-10 검증 방법).

가상 위키 200문서 각각에 수정 시나리오를 한 번씩 적용하고, 증분 색인(incremental.apply_event)이 새로 임베딩한
청크 수를 그 문서를 통째로 다시 임베딩했을 때의 청크 수와 비교한다.

임베딩 호출 수는 모델과 무관하므로 가짜 인코더로 센다. 청크 분할은 실제 토크나이저(bge-m3)를 쓴다.
검색 저장소는 로컬 PostgreSQL에 실험용 테이블을 만들었다가 지운다.

    uv run python experiments/m3-indexing/savings.py
"""

import hashlib
import json
import random
import sys
import uuid
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.fakewiki import FakeWiki, Page, Space  # noqa: E402
from wiki_rag_mcp.config import Settings  # noqa: E402
from wiki_rag_mcp.indexing.chunker import split_sections  # noqa: E402
from wiki_rag_mcp.indexing.embedder import TokenCounter  # noqa: E402
from wiki_rag_mcp.indexing.events import Event  # noqa: E402
from wiki_rag_mcp.indexing.incremental import apply_event  # noqa: E402
from wiki_rag_mcp.models import Classification  # noqa: E402
from wiki_rag_mcp.search.pg_store import PgStore  # noqa: E402
from wiki_rag_mcp.wiki.files import FileWikiSource  # noqa: E402

DIM = 8
SEED = 20260927
WIKI_DIR = ROOT / "data" / "wiki"


class CountingEncoder:
    model_name, revision, dtype = "count-only", "0", "float32"

    def __init__(self):
        self.encoded = 0

    def encode_documents(self, texts):
        self.encoded += len(texts)
        vecs = [np.frombuffer(hashlib.sha256(t.encode()).digest()[:DIM], dtype=np.uint8).astype(np.float32) + 1
                for t in texts]
        return np.array([v / np.linalg.norm(v) for v in vecs])


def load_wiki() -> FakeWiki:
    schema = yaml.safe_load((WIKI_DIR / "schema.yaml").read_text(encoding="utf-8"))
    wiki = FakeWiki({name: Space(s["title"], tuple(s.get("principals", [])),
                                 Classification(s.get("classification", "general")))
                     for name, s in schema["spaces"].items()})
    for doc in FileWikiSource(WIKI_DIR).documents():
        wiki.pages[doc.doc_id] = Page(doc.space, doc.title, doc.body, doc.restricted_principals,
                                      version=doc.version, revision=doc.revision, updated_at=doc.updated_at)
    return wiki


# 수정 시나리오. 각 함수는 문서 하나를 바꾸고 위키가 낼 이벤트를 돌려준다

def typo(wiki, doc_id, rng):
    """섹션 하나 끝에 문장 하나를 덧붙인다. 위키 수정의 가장 흔한 형태다."""
    page = wiki.pages[doc_id]
    section = rng.choice(split_sections(page.body, page.title))
    body = page.body.replace(section.text, section.text + "\n\n수정 내용을 덧붙였습니다.", 1)
    return wiki.edit(doc_id, body=body)


def add_section(wiki, doc_id, rng):
    body = wiki.pages[doc_id].body.rstrip() + "\n\n## 추가 안내\n\n문의는 담당 팀 채널로 합니다.\n"
    return wiki.edit(doc_id, body=body)


def restrict(wiki, doc_id, rng):
    return wiki.restrict(doc_id, ("group:leads",))


def classify(wiki, doc_id, rng):
    return wiki.classify(doc_id, Classification.CONFIDENTIAL)


def rename(wiki, doc_id, rng):
    """제목을 바꾼다. 본문 첫 줄의 제목도 같이 바꿔 청크 id는 그대로 둔다."""
    page = wiki.pages[doc_id]
    title = page.title + " (개정)"
    return wiki.edit(doc_id, title=title, body=page.body.replace(f"# {page.title}\n", f"# {title}\n", 1))


SCENARIOS = {"typo": typo, "add_section": add_section, "restrict": restrict, "classify": classify, "rename": rename}


def run(name, change, count) -> dict:
    wiki = load_wiki()
    store = PgStore.from_settings(Settings(index_alias=f"m3_savings_{uuid.uuid4().hex[:8]}"))
    store.ensure_index(dim=DIM)
    rng = random.Random(SEED)
    encoder = CountingEncoder()
    try:
        doc_ids = sorted(wiki.pages)
        for doc_id in doc_ids:
            apply_event(Event(doc_id, wiki.pages[doc_id].revision, "CONTENT_CHANGED"), wiki, store, encoder, count)
        full = encoder.encoded  # 코퍼스 전체를 한 번 임베딩한 청크 수
        whole_doc = embedded = 0
        for doc_id in doc_ids:
            whole_doc += store.conn.execute(f"SELECT count(*) FROM {store.table} WHERE doc_id = %s",
                                            [doc_id]).fetchone()[0]
            before = encoder.encoded
            applied = apply_event(change(wiki, doc_id, rng), wiki, store, encoder, count)
            assert applied.outcome == "indexed", applied
            embedded += encoder.encoded - before
        return {"scenario": name, "documents": len(doc_ids), "corpus_chunks": full,
                "whole_doc_chunks": whole_doc, "embedded": embedded,
                "saving_vs_whole_doc": round(1 - embedded / whole_doc, 3)}
    finally:
        store.drop()


def main():
    count = TokenCounter()
    results = [run(name, change, count) for name, change in SCENARIOS.items()]
    out = Path(__file__).parent / "results" / "savings.jsonl"
    out.parent.mkdir(exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    for r in results:
        print(r)


if __name__ == "__main__":
    main()
