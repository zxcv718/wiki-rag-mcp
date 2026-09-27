"""설계서(docs/design.md)의 ADR 절로 docs/adr/ 파일과 목록을 만든다.

유효한 ADR의 원본은 설계서다. 상태가 "대체됨"인 ADR은 설계서 본문에 없고
파일로만 남으므로 본문은 건드리지 않고 "- 상태:" 줄만 결정 목록에 맞춘다.

    python3 scripts/adr_sync.py          # 파일을 다시 만든다
    python3 scripts/adr_sync.py --check  # 어긋난 파일이 있으면 exit 1 (CI용)
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "docs" / "design.md"
ADR_DIR = ROOT / "docs" / "adr"

ROW_START = re.compile(r"^\|\s*ADR-\d+")
ROW = re.compile(r"^\|\s*(ADR-\d+)\s*\((\d+)장\)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")
SECTION = re.compile(r"^### (ADR-\d+)\. (.+)$")
CHAPTER = re.compile(r"^## ((\d+)\. .+)$")
HEADING = re.compile(r"^#{1,3} ")
FENCE = re.compile(r"^\s*(```|~~~)")
STATUS_LINE = re.compile(r"^- 상태: .*$", re.M)
ADR_FILE = re.compile(r"^(ADR-\d+)\.md$")

README_HEAD = """# 결정 기록 (ADR) 목록

형식: 맥락, 선택지, 결정, 감수한 비용, 재검토 조건. \
결정이 바뀌면 기존 파일을 지우지 않고 상태를 `대체됨`으로 바꾼 뒤 새 ADR을 추가합니다.

이 폴더의 파일은 `scripts/adr_sync.py`가 설계서(`docs/design.md`)에서 만듭니다. 고칠 때는 설계서를 고칩니다.

| ID | 결정 | 상태 |
|---|---|---|
"""


def parse(text, errors):
    rows, sections = {}, {}
    chapter, chapter_no, current, in_fence = None, None, None, False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and HEADING.match(line):
            current = None
            if line.startswith("## "):
                m = CHAPTER.match(line)
                chapter, chapter_no = (m.group(1), m.group(2)) if m else (None, None)
            elif m := SECTION.match(line):
                current = m.group(1)
                if current in sections:
                    errors.append(f"{current}: 설계서에 절이 두 번 있음 ({n}행)")
                sections[current] = {"title": m.group(2), "chapter": chapter,
                                     "chapter_no": chapter_no, "body": []}
                continue
        elif not in_fence and ROW_START.match(line):
            m = ROW.match(line)
            if not m:
                errors.append(f"결정 목록 {n}행의 형식이 다름: {line}")
            elif m.group(1) in rows:
                errors.append(f"{m.group(1)}: 결정 목록에 두 번 있음 ({n}행)")
            else:
                rows[m.group(1)] = {"chapter_no": m.group(2), "decision": m.group(3),
                                    "alternatives": m.group(4), "status": m.group(5)}
        if current:
            sections[current]["body"].append(line)
    return rows, sections


def render(adr_id, row, sec):
    body = "\n".join(sec["body"]).strip("\n")
    return (f"# {adr_id}. {sec['title']}\n\n"
            f"- 상태: {row['status']}\n"
            f"- 주요 대안: {row['alternatives']}\n"
            f"- 출처: 설계서 {sec['chapter']}\n\n"
            f"{body}\n")


def build():
    errors = []
    rows, sections = parse(DESIGN.read_text(encoding="utf-8"), errors)
    files = {}
    for adr_id, row in rows.items():
        sec = sections.get(adr_id)
        path = ADR_DIR / f"{adr_id}.md"
        if row["status"].startswith("대체됨"):
            if sec:
                errors.append(f"{adr_id}: 대체된 ADR이 설계서 본문에 남아 있음")
            if not path.exists():
                errors.append(f"{adr_id}: 대체된 ADR의 파일이 없음")
                continue
            old = path.read_text(encoding="utf-8")
            if not STATUS_LINE.search(old):
                errors.append(f"{adr_id}: 파일에 '- 상태:' 줄이 없음")
                continue
            status_line = f"- 상태: {row['status']}"
            files[path.name] = STATUS_LINE.sub(lambda _, line=status_line: line, old, count=1)
            continue
        if not sec:
            errors.append(f"{adr_id}: 결정 목록에는 있지만 설계서에 절이 없음")
            continue
        if sec["chapter_no"] != row["chapter_no"]:
            errors.append(f"{adr_id}: 결정 목록은 {row['chapter_no']}장, 본문은 {sec['chapter_no']}장")
        files[path.name] = render(adr_id, row, sec)
    for adr_id in sections.keys() - rows.keys():
        errors.append(f"{adr_id}: 설계서에 절은 있지만 결정 목록에 없음")
    for path in ADR_DIR.glob("ADR-*.md"):
        m = ADR_FILE.match(path.name)
        if m and m.group(1) not in rows:
            errors.append(f"{path.name}: 결정 목록에 없는 파일")

    order = sorted(rows, key=lambda i: int(i.split("-")[1]))
    files["README.md"] = README_HEAD + "".join(
        f"| [{i}]({i}.md) | {rows[i]['decision']} | {rows[i]['status']} |\n" for i in order)
    return files, errors


def main():
    check = "--check" in sys.argv[1:]
    files, errors = build()
    for name, content in files.items():
        path = ADR_DIR / name
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if check:
            errors.append(f"{name}: 설계서와 다름 (python3 scripts/adr_sync.py 실행 필요)")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"갱신: docs/adr/{name}")
    for e in errors:
        print(f"오류: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
