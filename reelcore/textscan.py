"""文本禁用词扫描：brief、plan 里的可见文字、字幕、显式附加文本。

只证明「这些输入文本」不含禁用词；PNG、录屏、成片画面里的字没有 OCR，不在覆盖范围内。
"""
import json, os, re
from . import subtitles

PLAN_TEXT_KEYS = {"text", "title", "caption", "label"}


def _walk_strings(obj, path=""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")


def _text_key_strings(obj, path, skip=()):
    """任意层级下键名属于 PLAN_TEXT_KEYS 的字符串；skip 里的键名整棵子树不进。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip:
                continue
            p = f"{path}.{k}" if path else k
            if k in PLAN_TEXT_KEYS and isinstance(v, str):
                yield p, v
            else:
                yield from _text_key_strings(v, p, skip)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _text_key_strings(v, f"{path}[{i}]", skip)


def _plan_strings(plan):
    yield from _text_key_strings(plan.get("shots") or [], "shots")


def _brief_strings(b):
    """brief 里会上屏的字段：name、must_not_change、可见文字键；voice 子树和配置值不扫。"""
    if not isinstance(b, dict):
        return []
    out = []
    if isinstance(b.get("name"), str):
        out.append(("name", b["name"]))
    if "must_not_change" in b:
        out.extend(_walk_strings(b["must_not_change"], "must_not_change"))
    out.extend(_text_key_strings(b, "", skip=("voice", "must_not_change")))
    return out


def collect(brief=None, plan=None, plan_subs=None, subs=(), texts=()):
    """返回 [(kind, 文件, [(位置, 文本)]) 或 (kind, 文件, None=缺文件)]。"""
    out = []
    if brief:
        try:
            with open(brief, encoding="utf-8") as fh:
                b = json.load(fh)
            out.append(("brief", brief, _brief_strings(b)))
        except (OSError, ValueError):
            out.append(("brief", brief, None))
    if plan is not None:
        out.append(("plan", "plan.shots", list(_plan_strings(plan))))
    for kind, files in (("subtitles", ([plan_subs] if plan_subs else []) + list(subs)), ("text", texts)):
        for f in files:
            if not os.path.isfile(f):
                out.append((kind, f, None))
                continue
            try:
                if kind == "subtitles":
                    lines = [(f"{a:.2f}s", t) for a, _, t in subtitles.cues(subtitles.parse(f))]
                else:
                    with open(f, encoding="utf-8") as fh:
                        lines = [(f"L{i}", s) for i, s in enumerate(fh.read().splitlines(), 1)]
            except Exception:  # noqa: BLE001  读不了等同缺失，不能当作扫过
                lines = None
            out.append((kind, f, lines))
    return out


def _covered(low, allow):
    covered = set()
    for a in allow:
        for m in re.finditer(re.escape(a.lower()), low):
            covered.update(range(m.start(), m.end()))
    return covered


def _word_re(w, ignore_case):
    """整词匹配：边界固定为 ASCII [A-Za-z0-9_] 且区分大小写；ignore_case 只作用于词本身（(?i:…)），
    避免 İ ı ſ K（K）这类大小写折叠成 ASCII 的字符被当成边界字母。空白匹配任意空白。"""
    if ignore_case:
        body = "(?i:" + r"\s+".join(re.escape(x) for x in w.split()) + ")"
    else:
        body = re.escape(w)
    return re.compile(r"(?<![A-Za-z0-9_])" + body + r"(?![A-Za-z0-9_])")


def _hits(text, banned, allow, banned_words=(), review=()):
    """返回 [(词, 位置, severity)]；banned 子串不分大小写，banned_words 区分大小写整词，review 不分大小写整词。"""
    low = text.lower()
    # 每组匹配与 allow 区间必须在同一坐标系：子串类在 lower() 文本上，整词类在原文上
    # （lower() 可能改变长度，如 "İ" → 2 个码点，两套坐标不能混用）。
    cov_low = _covered(low, allow)
    cov_orig = set()
    for a in allow:
        for m in re.finditer(re.escape(a), text, re.IGNORECASE):
            cov_orig.update(range(m.start(), m.end()))
    pats = [(w, re.compile(re.escape(w.lower())), "fail", low, cov_low) for w in banned]
    pats += [(w, _word_re(w, False), "fail", text, cov_orig) for w in banned_words]
    pats += [(w, _word_re(w, True), "review", text, cov_orig) for w in review]
    hits = []
    for w, rx, sev, hay, covered in pats:
        for m in rx.finditer(hay):
            if not all(i in covered for i in range(m.start(), m.end())):
                hits.append((w, m.start(), sev))
    return hits


def scan(sources, profile):
    t = profile.get("text") or {}
    banned = list(dict.fromkeys((t.get("banned") or []) + (t.get("extra_banned") or [])))
    banned_words = list(dict.fromkeys(t.get("banned_words") or []))
    review = list(dict.fromkeys(t.get("review") or []))
    allow = t.get("allow") or []
    require = set(t.get("require") or [])
    rule = (f"禁用词 {len(banned)} 个（profile text.banned + extra_banned，不分大小写子串）和 {len(banned_words)} 个"
            "整词（text.banned_words，区分大小写，前后不紧邻字母数字下划线）→ fail；"
            f"待人工确认词 {len(review)} 个（text.review，不分大小写整词）→ needs_review；"
            "text.allow 覆盖的短语放行；缺失或读不了的输入文件 → fail；只覆盖输入文本，画面里的字没有 OCR")
    if not sources and not require:
        return {"status": "not_checked", "rule": rule, "measured": {"sources": 0},
                "evidence": [], "note": "没有提供可扫描的文本（--brief / --plan 字幕 / --subs / --text）"}
    ev, n_lines = [], 0
    kinds = {k for k, _, lines in sources if lines is not None}
    for kind, f, lines in sources:
        if lines is None:
            ev.append({"kind": kind, "file": f, "missing": True, "severity": "fail"})
            continue
        for where, s in lines:
            n_lines += 1
            for w, pos, sev in _hits(s, banned, allow, banned_words, review):
                ev.append({"kind": kind, "file": f, "at": where, "word": w,
                           "context": s[max(0, pos - 12): pos + len(w) + 12], "severity": sev})
    for k in sorted(require - kinds):
        ev.append({"kind": k, "missing": True, "required_by_profile": True, "severity": "fail"})
    failing = any(e.get("severity", "fail") == "fail" for e in ev)
    status = "fail" if failing else ("needs_review" if ev else "pass")
    return {"status": status, "rule": rule,
            "measured": {"sources": len(sources), "lines": n_lines, "banned": len(banned),
                         "banned_words": len(banned_words), "review": len(review), "kinds": sorted(kinds)},
            "evidence": ev}
