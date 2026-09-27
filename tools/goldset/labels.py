"""블라인드 라벨과 출처 라벨을 합친다 (ADR-14 편향 대응).

질문의 출처 문서(생성 기록)와, 출처를 모르는 라벨러가 위키 전체에서 찾은 문서를 비교한다.

- 일치하면 출처 문서와, 라벨러가 같은 답을 찾은 다른 문서를 모두 정답으로 둔다.
- 다르면 검토 목록에 올린다. 검토 결과는 overrides.yaml에 적고 다시 합친다.
- 모순·개정 문항의 정답은 새 문서 하나다 (ADR-20). 질문자가 볼 수 없는 문서는 정답에서 뺀다.
"""

import json
import random
from pathlib import Path
from typing import Any

from tools.wikigen.spec import Spec


def export_blind(spec: Spec, sources: list[dict[str, Any]], questions: dict[str, str], parts: int,
                 out_dir: Path, seed: int) -> list[Path]:
    """출처와 유형을 뺀 질문 목록. 라벨러가 섞인 순서로 받게 해 유형을 짐작하지 못하게 한다."""
    rows = [{"id": s["id"], "question": questions[s["id"]],
             "asker_team": spec.team_names([f"group:{g}" for g in spec.users[s["asker"]]])} for s in sources]
    random.Random(seed).shuffle(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for n in range(parts):
        path = out_dir / f"blind-{n + 1}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[n::parts]), encoding="utf-8")
        paths.append(path)
    return paths


def merge(spec: Spec, manifest: dict[str, Any], sources: list[dict[str, Any]], questions: dict[str, str],
          blind: dict[str, dict[str, Any]], overrides: dict[str, dict[str, Any]]
          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """골든셋 문항과 검토할 문항 목록. 검토할 문항은 overrides에 결정이 있어야 골든셋에 들어간다."""
    docs = {d["doc_id"]: d for d in manifest["documents"]}

    def visible(user: str, doc_id: str) -> bool:
        d = docs[doc_id]
        return spec.can_see(user, spec.spaces[d["space"]]["principals"], d["restricted"])

    golden, review = [], []
    for s in sources:
        qid, kind = s["id"], s["type"]
        label = blind.get(qid)
        found = [d["doc_id"] for d in (label or {}).get("docs", []) if d.get("doc_id") in docs]
        problems = []
        relevant = [s["doc_id"]] if s["doc_id"] else []
        if label is None:
            problems.append("블라인드 라벨이 없다")
        elif kind == "no_answer":
            relevant = []
            if found:
                problems.append("답 없음 문항인데 라벨러가 답을 찾았다")
        elif kind == "contradiction":
            relevant = [s["doc_id"]]  # 정답은 생성 기록의 새 문서 (ADR-20)
            if s["doc_id"] not in found:
                problems.append("라벨러가 새 문서를 찾지 못했다")
        else:
            relevant = [s["doc_id"], *(d for d in found if d != s["doc_id"])]
            if s["doc_id"] not in found:
                problems.append("라벨러가 출처 문서를 찾지 못했다")
            elif label.get("conflict"):
                problems.append("라벨러가 찾은 문서끼리 답이 다르다")
        if label is not None and label.get("ambiguous"):
            problems.append("라벨러가 질문이 모호하다고 했다")

        override = overrides.get(qid)
        if override is not None:
            if override.get("drop"):
                continue
            relevant = override.get("relevant", relevant)
            question = override.get("question", questions[qid])
        elif problems:
            review.append({"id": qid, "type": kind, "question": questions[qid], "asker": s["asker"],
                           "source_doc": s["doc_id"], "source_fact": s.get("fact") or s.get("change") or s.get("topic"),
                           "blind": label, "problems": problems})
            continue
        else:
            question = questions[qid]
        golden.append({
            "id": qid, "type": kind, "question": question, "asker": s["asker"],
            "relevant": [d for d in dict.fromkeys(relevant) if visible(s["asker"], d)],
            "source_doc": s["doc_id"],
        })
    return golden, review
