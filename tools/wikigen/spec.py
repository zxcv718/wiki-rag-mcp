"""생성 스키마(schema.yaml)와 심을 사례(plants.yaml)를 읽고, 둘이 서로 맞는지 확인한다."""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ALL = "all"
# PRJ-204, SP-E2003처럼 생긴 사내 코드. 사전에 없는 코드를 LLM이 지어내면 약어 문항의 정답이 흐려진다
# 한국어 조사가 바로 붙으므로(PRJ-204가) \b 대신 앞뒤 문자를 직접 본다
CODE = re.compile(r"(?<![A-Za-z0-9-])[A-Z]{2,4}-[A-Z]?\d{3,4}(?![0-9])")
GENERIC_CODES = {"ISO-8601"}


@dataclass
class Spec:
    schema: dict[str, Any]
    plants: dict[str, dict[str, Any]]  # 심을 문서의 키 -> 내용
    pairs: list[dict[str, Any]]
    targets: dict[str, int]

    @property
    def today(self) -> date:
        return self.schema["today"]

    @property
    def spaces(self) -> dict[str, dict[str, Any]]:
        return self.schema["spaces"]

    @property
    def users(self) -> dict[str, list[str]]:
        return self.schema["users"]

    @property
    def glossary(self) -> list[dict[str, str]]:
        return self.schema["glossary"]

    @property
    def groups(self) -> set[str]:
        return {o["group"] for o in self.schema["org"]}

    @property
    def absent_keywords(self) -> list[str]:
        return [k for t in self.schema.get("absent_topics", []) for k in t["keywords"]]

    def unknown_codes(self, text: str) -> list[str]:
        known = {g["term"] for g in self.glossary} | GENERIC_CODES
        return sorted({c for c in CODE.findall(text) if c not in known})

    def principals_of(self, user: str) -> set[str]:
        return {ALL, f"user:{user}", *(f"group:{g}" for g in self.users[user])}

    def can_see(self, user: str, space_principals, restricted) -> bool:
        """두 층을 모두 만족해야 보인다 (ADR-21). 빈 목록은 아무도 만족하지 못한다."""
        mine = self.principals_of(user)
        return bool(mine & set(space_principals)) and bool(mine & set(restricted))

    def team_names(self, principals) -> str:
        """권한 목록을 사람이 읽는 팀 이름으로 바꾼다. 프롬프트에 쓴다."""
        names = {o["group"]: o["team"] for o in self.schema["org"]}
        out = []
        for p in principals:
            if p == ALL:
                out.append("협력사를 포함한 모든 사람")
            elif p.startswith("group:"):
                out.append(names.get(p[6:], p))
            else:
                out.append(f"사용자 {p[5:]}")
        return ", ".join(out) or "아무도 없음"


def load_spec(schema_path: Path, plants_path: Path) -> Spec:
    schema = yaml.safe_load(Path(schema_path).read_text(encoding="utf-8"))
    plants = yaml.safe_load(Path(plants_path).read_text(encoding="utf-8"))
    spec = Spec(schema=schema, plants=plants["docs"], pairs=plants["pairs"], targets=plants["targets"])
    problems = validate_spec(spec)
    if problems:
        raise ValueError("생성 스키마 오류:\n" + "\n".join(f"- {p}" for p in problems))
    return spec


def _principal_ok(spec: Spec, p: str) -> bool:
    if p == ALL:
        return True
    kind, _, name = p.partition(":")
    return (kind == "group" and name in spec.groups) or (kind == "user" and name in spec.users)


def validate_spec(spec: Spec) -> list[str]:
    problems = []
    terms = {g["term"] for g in spec.glossary}
    for user, groups in spec.users.items():
        problems += [f"사용자 {user}: 조직도에 없는 그룹 {g}" for g in groups if g not in spec.groups]
    for sid, space in spec.spaces.items():
        problems += [f"스페이스 {sid}: 알 수 없는 권한 {p}" for p in space["principals"] if not _principal_ok(spec, p)]
    problems += [f"용어 {g['term']}: 없는 스페이스 {g['space']}"
                 for g in spec.glossary if g["space"] not in spec.spaces]

    per_space: dict[str, int] = {}
    for key, p in spec.plants.items():
        where = f"심을 문서 {key}"
        if p["space"] not in spec.spaces:
            problems.append(f"{where}: 없는 스페이스 {p['space']}")
            continue
        per_space[p["space"]] = per_space.get(p["space"], 0) + 1
        if p["updated"] > spec.today:
            problems.append(f"{where}: 수정일이 기준일보다 늦음")
        this_month = (p["updated"].year, p["updated"].month) == (spec.today.year, spec.today.month)
        if this_month and not p.get("change"):
            problems.append(f"{where}: 이번 달에 수정한 문서인데 change(바뀐 내용)가 없음")
        for fact in p["facts"]:
            if len(fact) != 2 or fact[1] not in fact[0]:
                problems.append(f"{where}: 핵심 사실 형식 오류 {fact}")
        problems += [f"{where}: 사전에 없는 용어 {t}" for t in p.get("terms", []) if t not in terms]
        problems += [f"{where}: 알 수 없는 권한 {r}" for r in p.get("restricted", [ALL]) if not _principal_ok(spec, r)]
    for sid, n in per_space.items():
        if n > spec.spaces[sid]["docs"]:
            problems.append(f"스페이스 {sid}: 심을 문서 {n}개가 문서 수 {spec.spaces[sid]['docs']}보다 많음")

    for pair in spec.pairs:
        old, new = spec.plants.get(pair["old"]), spec.plants.get(pair["new"])
        if not old or not new:
            problems.append(f"쌍 {pair['id']}: 심을 문서에 없는 키")
            continue
        if old["updated"] >= new["updated"]:
            problems.append(f"쌍 {pair['id']}: 옛 문서가 새 문서보다 늦게 수정됨")
        if pair["old_value"] not in {f[1] for f in old["facts"]}:
            problems.append(f"쌍 {pair['id']}: old_value가 옛 문서의 핵심 사실에 없음")
        if pair["new_value"] not in {f[1] for f in new["facts"]}:
            problems.append(f"쌍 {pair['id']}: new_value가 새 문서의 핵심 사실에 없음")
    return problems
