"""유형별 문항 수(ADR-20)에 맞춰 질문의 출처와 질문자를 고른다. LLM을 부르지 않고 시드로 정한다.

출처는 생성 기록(manifest.yaml)의 핵심 사실이다. 질문자는 그 문서를 볼 수 있는 가상 사용자 가운데
지금까지 덜 뽑힌 사람을 고른다. 판정 실험도 권한 필터를 켠 채 하므로(ADR-20), 질문자가 볼 수 없는
문서를 정답으로 두지 않는다.
"""

import random
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from tools.wikigen.spec import CODE, Spec

QUOTAS = {"fact": 90, "procedure": 60, "abbreviation": 45, "recency": 30, "permission": 30, "contradiction": 15,
          "no_answer": 30}
# 절차 질문("어떻게 해?", "누가 해?")의 출처가 되는 문서 유형
PROCEDURE_DOC_TYPES = {"절차 안내", "응대 매뉴얼", "런북", "조회 가이드", "설치 가이드", "온보딩", "문제 해결",
                       "사고 대응 절차", "파이프라인 문서", "개발 가이드"}
PERMISSION_SPACES = {"security", "hr-internal", "leadership"}  # 한 팀만 보는 기밀 스페이스
MAX_PER_DOC = 2  # 한 문서에서 너무 많은 문항이 나오면 문항끼리 더 닮는다 (ADR-20 감수한 비용)
SEED_OFFSET = 7  # 위키 생성과 다른 난수열을 쓴다

HEAD = """\
# 골든셋 출처 (7장 골든셋 구성). tools/goldset select가 만든다. 손으로 고치지 않는다.
# 문항마다 유형, 질문자, 출처 문서와 핵심 사실을 적는다. 질문 작성자와 블라인드 라벨러에게는 이 파일을 주지 않는다.
"""


class Picker:
    def __init__(self, spec: Spec, manifest: dict[str, Any]):
        self.spec = spec
        self.rng = random.Random(spec.schema["seed"] + SEED_OFFSET)
        self.docs = {d["doc_id"]: d for d in manifest["documents"]}
        self.viewers = {
            i: [u for u in spec.users if spec.can_see(u, spec.spaces[d["space"]]["principals"], d["restricted"])]
            for i, d in self.docs.items()
        }
        self.pairs = manifest["pairs"]
        self.old_side = {p["old"] for p in self.pairs}
        self.used_facts: set[tuple[str, int]] = set()
        self.per_doc: Counter[str] = Counter()
        self.asked: Counter[str] = Counter()
        self.picked: list[dict[str, Any]] = []

    def asker(self, pool: list[str]) -> str:
        """후보 가운데 지금까지 가장 덜 물은 사람. 사용자별 문항 수를 고르게 한다."""
        fewest = min(self.asked[u] for u in pool)
        user = self.rng.choice(sorted(u for u in pool if self.asked[u] == fewest))
        self.asked[user] += 1
        return user

    def open_facts(self, doc_id: str) -> list[int]:
        return [i for i in range(len(self.docs[doc_id]["key_facts"])) if (doc_id, i) not in self.used_facts]

    def usable(self, doc_id: str) -> bool:
        return bool(self.viewers[doc_id]) and doc_id not in self.old_side and self.per_doc[doc_id] < MAX_PER_DOC

    def add(self, kind: str, doc_id: str | None, fact_index: int | None = None, pool: list[str] | None = None,
            **extra) -> None:
        pool = pool or (self.viewers[doc_id] if doc_id else list(self.spec.users))
        record: dict[str, Any] = {"type": kind, "asker": self.asker(pool), "doc_id": doc_id,
                                  "fact": None, "must_include": None, **extra}
        if doc_id is not None:
            self.per_doc[doc_id] += 1
            if fact_index is not None:
                self.used_facts.add((doc_id, fact_index))
                fact = self.docs[doc_id]["key_facts"][fact_index]
                record.update(fact=fact["fact"], must_include=fact["must_include"])
        self.picked.append(record)

    def pick_facts(self, kind: str, n: int, doc_ids: list[str]) -> None:
        """후보 문서마다 사실 하나씩 먼저 고르고, 모자라면 두 번째 사실을 고른다."""
        order = sorted(doc_ids)
        self.rng.shuffle(order)
        for _ in range(MAX_PER_DOC):
            for doc_id in order:
                if n == 0:
                    return
                facts = self.open_facts(doc_id)
                if self.usable(doc_id) and facts:
                    self.add(kind, doc_id, self.rng.choice(facts))
                    n -= 1
        if n:
            raise ValueError(f"{kind}: 후보가 {n}개 모자란다")


def select(spec: Spec, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    p = Picker(spec, manifest)
    docs = p.docs

    # 모순·개정: 쌍마다 한 문항. 정답은 새 문서다 (ADR-20). 가능하면 옛 문서도 보이는 사람이 묻는다
    for pair in p.pairs:
        new = docs[pair["new"]]
        index = next(i for i, f in enumerate(new["key_facts"]) if f["must_include"] == pair["new_value"])
        both = [u for u in p.viewers[pair["new"]] if u in p.viewers[pair["old"]]]
        p.add("contradiction", pair["new"], index, pool=both or None, topic=pair["topic"])

    # 최신성: 이번 달에 바뀐 문서마다 "무엇이 바뀌었나". 모자란 만큼은 같은 문서의 다른 사실로 채운다
    recent = sorted(i for i, d in docs.items() if "recent" in d["intents"] and p.viewers[i])
    for doc_id in recent:
        p.add("recency", doc_id, None, change=docs[doc_id]["change"])
    p.pick_facts("recency", QUOTAS["recency"] - len(recent), recent)

    # 권한 제한: 문서 제한이 걸린 문서와 한 팀만 보는 기밀 스페이스의 문서
    restricted = [i for i, d in docs.items() if "restricted" in d["intents"]]
    team_only = [i for i, d in docs.items() if d["space"] in PERMISSION_SPACES and i not in restricted]
    p.pick_facts("permission", len(restricted), restricted)
    p.pick_facts("permission", QUOTAS["permission"] - len(restricted), team_only)

    # 약어·고유명사: 용어마다 한 문항, 그 용어를 설명할 스페이스의 문서를 먼저 쓴다. 남는 수는 에러 코드로
    terms = list(spec.glossary)
    codes = [g for g in terms if CODE.fullmatch(g["term"])]
    extra = QUOTAS["abbreviation"] - len(terms)
    for g in [*terms, *p.rng.sample(codes, extra)]:
        cands = [(i, k) for i, d in docs.items() if p.usable(i)
                 for k, f in enumerate(d["key_facts"]) if g["term"] in f["fact"] and (i, k) not in p.used_facts]
        home = [c for c in cands if docs[c[0]]["space"] == g["space"]]
        if not cands:
            raise ValueError(f"약어 {g['term']}: 출처가 될 핵심 사실이 없다")
        doc_id, index = p.rng.choice(sorted(home or cands))
        p.add("abbreviation", doc_id, index, term=g["term"])

    # 절차와 사실 조회: 남은 문서에서 고른다
    procedure = [i for i, d in docs.items() if d["doc_type"] in PROCEDURE_DOC_TYPES]
    p.pick_facts("procedure", QUOTAS["procedure"], procedure)
    p.pick_facts("fact", QUOTAS["fact"], [i for i in docs if i not in procedure] + procedure)

    # 답 없음: 위키에 없어야 하는 주제마다 두 문항
    for topic in spec.schema["absent_topics"]:
        for _ in range(QUOTAS["no_answer"] // len(spec.schema["absent_topics"])):
            p.add("no_answer", None, None, topic=topic["topic"])

    counts = Counter(r["type"] for r in p.picked)
    if counts != Counter(QUOTAS):
        raise ValueError(f"유형별 문항 수가 맞지 않는다: {dict(counts)}")
    p.rng.shuffle(p.picked)  # id 순서로 유형이 드러나지 않게 섞는다
    return [{"id": f"q{n:03d}", **r} for n, r in enumerate(p.picked, 1)]


def write_sources(path: Path, sources: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEAD + yaml.safe_dump(sources, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")


def load_sources(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
