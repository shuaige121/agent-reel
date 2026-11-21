#!/usr/bin/env python3
"""文档检查：README / SKILL / engine.zh 的 json 代码块合法，安装与 hook 范围的说法与代码一致。只读文档。"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ["README.md", "skill/SKILL.md", "docs/engine.zh.md"]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def json_blocks_parse():
    for rel in DOCS:
        blocks = re.findall(r"```json\n(.*?)```", read(rel), re.S)
        assert blocks, f"{rel}: no json blocks found"
        for k, b in enumerate(blocks):
            try:
                json.loads(b)
            except ValueError as e:
                raise AssertionError(f"{rel} json block {k}: {e}")


def install_uses_venv_and_shim():
    readme, skill = read("README.md"), read("skill/SKILL.md")
    for needle in ("~/.local/share/reel/venv", "reel doctor", "REEL_PYTHON", "plugins/reel/bin"):
        assert needle in readme, f"README missing {needle!r}"
    for rel, text in (("README.md", readme), ("skill/SKILL.md", skill), ("docs/engine.zh.md", read("docs/engine.zh.md"))):
        assert "python3 -m pip install pillow numpy" not in text, f"{rel}: bare pip install"
        assert not re.search(r"\breel\s*\(\s*\)\s*\{", text), f"{rel}: reel() function definition"
    assert "command -v reel" in skill and "plugins/reel/bin" in skill, "SKILL lacks PATH fallback line"


def hook_scope_documented():
    readme = read("README.md")
    assert "REEL_GATE_SCOPE=global" in readme
    assert "--out=" in readme
    assert "best-effort" in readme and "尽力而为" in readme
    assert "REEL_GATE_SCOPE=global" in read("skill/SKILL.md")
    assert "REEL_GATE_SCOPE=global" in read("docs/engine.zh.md")


def lyric_wording_is_narrow():
    for rel in DOCS:
        t = read(rel)
        assert "lyrics are on screen" not in t.lower(), f"{rel}: overclaiming lyric wording"
        assert "歌词上屏" not in t, f"{rel}: overclaiming lyric wording"
    assert "text layer" in read("README.md")
    assert "字层" in read("README.md") and "字层" in read("docs/engine.zh.md")


def optional_deps_documented():
    assert "pypinyin" in read("README.md")
    assert "pypinyin" in read("docs/engine.zh.md")


def text_scan_and_output_documented():
    for rel in ("README.md", "docs/engine.zh.md"):
        t = read(rel)
        for needle in ("banned_words", "review", "48 kHz", "ARG_INVALID", "umask"):
            assert needle in t, f"{rel} missing {needle!r}"
    readme = read("README.md")
    for name in ("test_output", "test_mvcheck", "test_textscan", "test_stopgate", "test_bashguard", "test_cli", "test_docs"):
        assert name in readme, f"README tests section lacks {name}"


def test_commands_use_venv_python():
    """tests import numpy/PIL, so the documented commands must not use a bare system python3."""
    for rel in DOCS:
        t = read(rel)
        assert not re.search(r"(^|[\s`$(])python3 (-B )?tests/", t, re.M), f"{rel}: bare `python3 tests/...` command"
    readme = read("README.md")
    assert readme.count("~/.local/share/reel/venv/bin/python tests/test_") >= 7, "README test commands lack venv interpreter"


def no_hardcoded_case_counts():
    assert not re.search(r"\d+\s*项故障注入", read("docs/engine.zh.md")), "stale hard-coded test count"


CASES = [json_blocks_parse, install_uses_venv_and_shim, hook_scope_documented, lyric_wording_is_narrow,
         optional_deps_documented, test_commands_use_venv_python, text_scan_and_output_documented, no_hardcoded_case_counts]

if __name__ == "__main__":
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"✓ {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"✗ {fn.__name__}: {e}")
    print(f"{len(CASES) - failed}/{len(CASES)} 通过")
    sys.exit(1 if failed else 0)
