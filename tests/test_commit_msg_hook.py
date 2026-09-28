"""커밋 메시지 형식 검사 훅(.githooks/commit-msg). 형식은 .gitmessage에 있다."""

import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent / ".githooks" / "commit-msg"


def check(tmp_path: Path, message: str) -> int:
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(message, encoding="utf-8")
    return subprocess.run(["bash", str(HOOK), str(path)], capture_output=True).returncode


@pytest.mark.parametrize("message", [
    "fix(deploy): 검색 이미지를 한 번만 빌드",
    "docs: 오타 수정",
    "feat(wiki)!: 이벤트 형식 바꾸기\n\nBREAKING CHANGE: 인덱서가 새 필드를 읽어야 한다",
    "# 템플릿 주석은 건너뛴다\n\nchore: 정리\n\nRefs: ADR-24\n",
    "Merge branch 'main' into m5",
    'Revert "fix(deploy): 검색 이미지를 한 번만 빌드"',
])
def test_accepts_conventional_subjects(tmp_path, message):
    assert check(tmp_path, message) == 0


@pytest.mark.parametrize("message", [
    "검색 이미지를 한 번만 빌드",
    "feature: 없는 타입",
    "fix(Deploy): 범위는 소문자",
    "fix:띄어 쓰지 않음",
    "fix(deploy): 제목\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    "fix: 제목\n\nGenerated with [Claude Code](https://claude.com/claude-code)",
])
def test_rejects_other_subjects_and_attribution(tmp_path, message):
    assert check(tmp_path, message) == 1
