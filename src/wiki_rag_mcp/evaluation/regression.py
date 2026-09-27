"""CI 회귀 테스트 (7장 "CI 회귀 테스트"). PR마다 골든셋 전체를 돌려, Recall@5가 기준값보다 2%p 이상 떨어지거나
권한 위반이 1건이라도 나오면 실패한다. 병합을 막는 경보라 ADR-20의 신뢰구간 규칙을 쓰지 않는다.

- 가상 위키를 임시 테이블에 새로 색인하고, 문항마다 질문자의 권한으로 pre-filter를 켜고 상위 10개를 찾는다.
  구성은 M2 기준선과 같다(벡터 검색, 맥락 헤더, 리랭커 없음).
- 권한 위반은 300문항 전체의 반환 결과를 생성 기록과 스키마(tools/wikigen/spec.py)로 따로 계산한 정답과 비교해
  센다. 서버의 권한 코드(filters.allows)도, 파일 위키 파서가 읽은 권한 필드도 쓰지 않는다.

임베딩은 파일 캐시(CachedEncoder)로 다시 쓴다. 모델 이름, 커밋, 형식을 고정했으므로(ADR-11) 같은 입력은 같은
벡터가 된다. 캐시가 건너뛰는 것은 이 계산뿐이고, 청크 분할, 색인, 권한 필터, 순위 매기기는 매번 실제로 돈다.
임베딩 코드(embedder.py), 고정값, 벡터 계산에 쓰는 라이브러리 버전 중 하나라도 바뀌면 캐시 전체를 버린다.
캐시가 모두 적중하면 2.2GB 모델을 불러오지 않는다.
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from wiki_rag_mcp.config import EMBEDDING_DIM, EMBEDDING_DTYPE, EMBEDDING_MODEL, EMBEDDING_REVISION, Settings

GATE = 0.02  # Recall@5가 기준값보다 이만큼 이상 떨어지면 막는다 (7장)
_EPS = 1e-9
# 같은 모델 파일이라도 이 라이브러리들이 바뀌면 토큰 분할이나 연산 결과가 달라질 수 있다
_LIBRARIES = ("torch", "transformers", "sentence-transformers", "tokenizers")
_SPEC = Path("tools/wikigen/spec.py")
_SHOWN_VIOLATIONS = 5


def embedding_namespace() -> str:
    """캐시 키에 섞는 값. 이 값이 바뀌면 이전 캐시를 하나도 쓰지 않는다."""
    from importlib.metadata import version

    from wiki_rag_mcp.indexing import embedder

    h = hashlib.sha256()
    for part in (EMBEDDING_MODEL, EMBEDDING_REVISION, EMBEDDING_DTYPE, *(f"{p}=={version(p)}" for p in _LIBRARIES)):
        h.update(part.encode() + b"\n")
    h.update(Path(embedder.__file__).read_bytes())
    return h.hexdigest()


def _cpu_embedder():
    from wiki_rag_mcp.indexing.embedder import Embedder

    return Embedder(device="cpu")  # GPU가 없는 CI 러너와 같은 장치


class CachedEncoder:
    """Embedder와 같은 인터페이스로, 임베딩을 .npz 파일에 캐시한다.

    키는 (namespace, 종류, 입력 문장)의 sha256이다. 종류는 문서(doc)와 질문(query)이라 같은 문장이어도 섞이지
    않는다. 모델은 캐시에 없는 입력이 처음 나올 때 factory로 만든다.
    """

    model_name = EMBEDDING_MODEL
    revision = EMBEDDING_REVISION
    dtype = EMBEDDING_DTYPE

    def __init__(self, path: Path, factory: Callable[[], Any] = _cpu_embedder, namespace: str | None = None):
        self.path = Path(path)
        self.namespace = namespace or embedding_namespace()
        self._factory = factory
        self._inner: Any = None
        self.hits = 0
        self.misses = 0
        self._vectors = self._load()
        self.loaded = len(self._vectors)

    def _load(self) -> dict[str, np.ndarray]:
        if not self.path.exists():
            return {}
        with np.load(self.path) as data:
            if str(data["namespace"]) != self.namespace:
                return {}
            return dict(zip(data["keys"].tolist(), data["vectors"], strict=True))

    def _key(self, kind: str, text: str) -> str:
        return hashlib.sha256(f"{self.namespace}\n{kind}\n{text}".encode()).hexdigest()

    def _encoder(self) -> Any:
        if self._inner is None:
            self._inner = self._factory()
        return self._inner

    @property
    def model_loaded(self) -> bool:
        return self._inner is not None

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        keys = [self._key("doc", t) for t in texts]
        missing = {k: t for k, t in zip(keys, texts, strict=True) if k not in self._vectors}
        miss_count = sum(k in missing for k in keys)
        self.misses += miss_count
        self.hits += len(keys) - miss_count
        if missing:  # 빠진 입력은 한 번에 임베딩한다
            vectors = self._encoder().encode_documents(list(missing.values()))
            for k, v in zip(missing, vectors, strict=True):
                self._vectors[k] = np.asarray(v, dtype=np.float32)
        if not keys:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        return np.stack([self._vectors[k] for k in keys])

    def encode_query(self, text: str) -> list[float]:
        # 질문은 서버처럼 한 문장씩 임베딩한다. 여러 문장을 묶으면 패딩 때문에 값이 조금 달라질 수 있다
        key = self._key("query", text)
        if key in self._vectors:
            self.hits += 1
        else:
            self.misses += 1
            self._vectors[key] = np.asarray(self._encoder().encode_query(text), dtype=np.float32)
        return self._vectors[key].tolist()

    def save(self) -> None:
        """임시 파일에 쓴 뒤 바꿔치기해서, 중간에 멈춰도 이전 캐시가 깨지지 않게 한다.

        메모리의 항목은 모두 지금 namespace로 만든 것이라, 다른 namespace의 항목은 파일에 남지 않는다.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        keys = list(self._vectors)
        vectors = np.stack([self._vectors[k] for k in keys]) if keys else np.empty((0, 0), dtype=np.float32)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                np.savez(f, namespace=np.array(self.namespace), keys=np.array(keys, dtype=str), vectors=vectors)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def passed(recall5: float, baseline_recall5: float, violations: int) -> bool:
    """병합해도 되는지. 7장은 "2%p 이상 떨어지면" 막으므로 딱 2%p 떨어진 경우도 실패다.

    0.97 - 0.95가 0.020000000000000018이 되는 것처럼 부동소수 뺄셈은 경계 근처에서 어느 쪽으로든 조금씩
    어긋난다. 비교에 1e-9의 여유를 두어, 2%p에 해당하는 값이면 계산 오차와 관계없이 늘 실패로 판단한다.
    """
    return violations == 0 and baseline_recall5 - recall5 < GATE - _EPS


def load_oracle(wiki_dir: Path) -> tuple[Any, dict[str, Any]]:
    """권한 정답을 계산하는 생성 스키마와 문서별 계획(권한·등급).

    tools는 설치 패키지에 들지 않아 저장소의 파일에서 직접 불러온다.
    """
    module_spec = importlib.util.spec_from_file_location("wikigen_spec", _SPEC)
    if module_spec is None or module_spec.loader is None:
        raise FileNotFoundError(_SPEC)
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module  # dataclass가 정의된 모듈을 찾을 수 있어야 한다
    module_spec.loader.exec_module(module)
    spec = module.load_spec(wiki_dir / "schema.yaml", _SPEC.with_name("plants.yaml"))
    return spec, module.ledger(spec, wiki_dir)


def check(golden_path: Path, baseline_path: Path, cache_path: Path, out_dir: Path) -> bool:
    """골든셋 전체로 회귀 검사를 하고 결과를 출력한다. 통과하면 True."""
    from wiki_rag_mcp.evaluation.report import summary
    from wiki_rag_mcp.evaluation.run import CONFIGS, Runner
    from wiki_rag_mcp.indexing.embedder import TokenCounter
    from wiki_rag_mcp.indexing.indexer import index_all
    from wiki_rag_mcp.search.pg_store import PgStore
    from wiki_rag_mcp.wiki.files import FileWikiSource

    started = time.perf_counter()
    settings = Settings.from_env()
    golden = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines()]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    source = FileWikiSource(settings.wiki_dir)
    oracle, planned = load_oracle(settings.wiki_dir)
    encoder = CachedEncoder(cache_path)
    # 서버 인덱스를 건드리지 않게 실행마다 새 테이블을 만들고 끝나면 지운다
    store = PgStore.from_settings(replace(settings, index_alias=f"ci-golden-{uuid.uuid4().hex[:8]}"))
    rows: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    try:
        store.ensure_index()
        stats = index_all(source, store, encoder, TokenCounter())
        indexed = time.perf_counter()
        runner = Runner(CONFIGS["baseline"], store, source, encoder)
        for q in golden:
            hits, timings = runner.search(q["question"], q["asker"])
            rows.append({"id": q["id"], "type": q["type"], "relevant": q["relevant"],
                         "results": [{"chunk_id": h["chunk_id"], "doc_id": h["doc_id"], "score": h["score"]}
                                     for h in hits], "timings": [timings]})
            for rank, h in enumerate(hits, 1):
                plan = planned.get(h["doc_id"])
                if plan is None or not oracle.can_see(q["asker"], plan.space_principals, plan.restricted_principals):
                    violations.append({"id": q["id"], "asker": q["asker"], "doc_id": h["doc_id"], "rank": rank})
        searched = time.perf_counter()
    finally:
        store.drop()
        store.conn.close()
        encoder.save()

    s = summary(rows)
    ok = passed(s["recall@5"], baseline["recall@5"], len(violations))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "golden.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                          encoding="utf-8")
    report = {"passed": ok, "questions": len(rows), "answerable": s["n"],
              **{m: s[m] for m in ("recall@5", "mrr@10", "ndcg@10")},
              "baseline": {m: baseline[m] for m in ("recall@5", "mrr@10", "ndcg@10")},
              "violations": violations, "cache": {"hits": encoder.hits, "misses": encoder.misses},
              "documents": stats.documents, "chunks": stats.chunks,
              "seconds": {"index": indexed - started, "search": searched - indexed,
                          "total": time.perf_counter() - started}}
    (out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    diff = (s["recall@5"] - baseline["recall@5"]) * 100
    print(f"골든셋 회귀 검사: 문항 {len(rows)}개 (지표는 정답이 있는 {s['n']}개)")
    print(f"  Recall@5 {s['recall@5']:.4f} (기준 {baseline['recall@5']:.4f}, 차이 {diff:+.2f}%p, "
          f"{GATE * 100:.0f}%p 이상 떨어지면 실패)")
    print(f"  MRR@10 {s['mrr@10']:.4f} (기준 {baseline['mrr@10']:.4f}), "
          f"nDCG@10 {s['ndcg@10']:.4f} (기준 {baseline['ndcg@10']:.4f})")
    print(f"  권한 위반 {len(violations)}건")
    for v in violations[:_SHOWN_VIOLATIONS]:
        print(f"    {v['id']} 질문자 {v['asker']}: {v['doc_id']} ({v['rank']}위)")
    if len(violations) > _SHOWN_VIOLATIONS:
        print(f"    외 {len(violations) - _SHOWN_VIOLATIONS}건 ({out_dir / 'summary.json'})")
    model = "모델을 불러옴" if encoder.model_loaded else "모델을 불러오지 않음"
    print(f"  임베딩 캐시 적중 {encoder.hits}, 미적중 {encoder.misses} (파일에서 {encoder.loaded}개 읽음, {model})")
    print(f"  색인 문서 {stats.documents}개, 청크 {stats.chunks}개")
    print(f"  걸린 시간 {report['seconds']['total']:.1f}초 (색인 {report['seconds']['index']:.1f}초, "
          f"검색 {report['seconds']['search']:.1f}초)")
    print(f"결과: {'통과' if ok else '실패'}")
    return ok
