"""`wiki-rag-index`: 가상 위키를 임베딩해 검색 인덱스에 넣는다."""

import argparse
from pathlib import Path

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.indexing.embedder import Embedder, TokenCounter
from wiki_rag_mcp.indexing.indexer import index_all
from wiki_rag_mcp.search.backend import open_store
from wiki_rag_mcp.wiki.files import FileWikiSource


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-dir", type=Path, help="가상 위키 디렉터리 (기본: WIKI_DIR 또는 data/wiki)")
    parser.add_argument("--reset", action="store_true", help="인덱스를 지우고 새로 만든다")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    source = FileWikiSource(args.wiki_dir or settings.wiki_dir)
    store = open_store(settings)
    if args.reset:
        store.drop()
    index_name = store.ensure_index()
    embedder = Embedder()
    stats = index_all(source, store, embedder, TokenCounter())
    print(f"색인 완료: 문서 {stats.documents}개, 청크 {stats.chunks}개, {stats.seconds:.1f}초 "
          f"({index_name}, {embedder.model_name}@{embedder.revision[:8]}, {embedder.dtype}, {embedder.device})")


if __name__ == "__main__":
    main()
