#!/usr/bin/env python3
"""文本扫描：禁用词三类规则与 brief 可见字段（无需 pytest）：python3 tests/test_textscan.py"""
import json, os, shutil, subprocess, sys, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from reelcore import profiles, textscan  # noqa: E402
from reelcore.errors import ReelError  # noqa: E402

TMP = tempfile.mkdtemp(prefix="reel_textscan_")
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def write(name, obj):
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))
    return p


def scan_lines(lines, prof=None):
    f = write("t.txt", "\n".join(lines) + "\n")
    return textscan.scan(textscan.collect(texts=[f]), prof or profiles.load())


def one(text, prof=None):
    return scan_lines([text], prof)


@case
def lowercase_todo_is_not_banned():
    for t in ("todo app 宣传", "Hello todo list", "TODOs", "MY_TODO", "mastodon"):
        r = one(t)
        assert r["status"] == "pass", (t, r)


@case
def uppercase_whole_words_fail():
    for t, w in (("TODO 换文案", "TODO"), ("(TBD)", "TBD"), ("FIXME: x", "FIXME"), ("TODO:", "TODO")):
        r = one(t)
        assert r["status"] == "fail", (t, r)
        e = r["evidence"][0]
        assert e["word"] == w and e["severity"] == "fail", e


@case
def placeholder_and_lorem_need_review():
    for t in ("placeholder", "Lorem  Ipsum dolor", "Put a PLACEHOLDER here"):
        r = one(t)
        assert r["status"] == "needs_review", (t, r)
        assert r["evidence"][0]["severity"] == "review", r
    assert one("placeholders")["status"] == "pass"
    assert one("TODO and placeholder")["status"] == "fail"
    m = one("x")["measured"]
    assert m["banned_words"] == 3 and m["review"] == 2, m


@case
def chinese_and_field_keep_substring():
    for t in ("价格待定", "占位图", "[field]"):
        assert one(t)["status"] == "fail", t


@case
def brief_scans_only_visible_fields():
    bad = "TODO 待定"
    quiet = {"version": "reel.brief.v1", "name": "清白片名", "language": bad, "delivery_root": bad,
             "deliverable": bad, "kind": bad, "profile": bad,
             "voice": {"approved": {"quote": bad, "stt": {"script": bad, "transcript": bad}, "title": bad}}}
    f = write("b_quiet.json", quiet)
    r = textscan.scan(textscan.collect(brief=f), profiles.load())
    assert r["status"] == "pass" and not r["evidence"], r
    loud = dict(quiet, name="待定片名", must_not_change=["保留 TODO 字幕", "ok"],
                scenes=[{"title": "TBD 标题"}])
    f = write("b_loud.json", loud)
    r = textscan.scan(textscan.collect(brief=f), profiles.load())
    ats = {e["at"] for e in r["evidence"]}
    assert ats == {"name", "must_not_change[0]", "scenes[0].title"}, ats
    assert r["status"] == "fail"
    f = write("b_missing_dir.json", "{not json")
    r = textscan.scan(textscan.collect(brief=f), profiles.load())
    assert r["status"] == "fail" and r["evidence"][0].get("missing"), r


@case
def allow_covers_all_classes():
    p = profiles.load()
    p["text"] = dict(p["text"], allow=["待定价", "TODO list", "placeholder text"])
    for t in ("待定价", "see TODO list", "placeholder text"):
        assert one(t, p)["status"] == "pass", t
    assert one("TODO list and TODO", p)["status"] == "fail"


@case
def allow_survives_length_changing_lowercase():
    p = profiles.load()
    p["text"] = dict(p["text"], allow=["TODO", "placeholder", "待定价"])
    for t in ("İ TODO", "İ placeholder", "İİİ TODO list", "ß TODO", "ẞ placeholder", "ẞ TODO",
              "ı TODO", "I\u0131 placeholder", "İ 待定价"):
        r = one(t, p)
        assert r["status"] == "pass", (t, r)
    for t, st in (("İ TODO TODO", "pass"), ("İ TODOx", "pass")):
        assert one(t, p)["status"] == st, t
    p["text"] = dict(p["text"], allow=["TODO list"])
    assert one("İ TODO list and TODO", p)["status"] == "fail"
    assert one("İ placeholder", p)["status"] == "needs_review"


@case
def word_boundary_is_ascii_only():
    for t in ("\u0130placeholder", "placeholder\u0131", "\u017fplaceholder", "\u212aplaceholder",
              "\u00e9placeholder", "\u4e2dplaceholder", "placeholder\u212a"):
        r = one(t)
        assert r["status"] == "needs_review" and r["evidence"][0]["severity"] == "review", (t, r)
    for t in ("TODO\u0131", "\u0131TODO", "\u0130TODO", "TODO\u212a"):
        r = one(t)
        assert r["status"] == "fail" and r["evidence"][0]["word"] == "TODO", (t, r)
    for t in ("Placeholder", "PLACEHOLDER", "a placeholder."):
        assert one(t)["status"] == "needs_review", t
    for t in ("placeholderx", "xplaceholder", "_placeholder", "placeholder9"):
        assert one(t)["status"] == "pass", t
    p = profiles.load()
    p["text"] = dict(p["text"], allow=["TODO", "placeholder"])
    assert one("\u0130 TODO", p)["status"] == "pass"
    assert one("\u0130 placeholder", p)["status"] == "pass"


@case
def project_banned_override_stays_substring():
    f = write("prof_draft.json", {"extends": "default", "text": {"banned": ["draft"]}})
    p = profiles.load(f)
    r = one("Draft v2", p)
    assert r["status"] == "fail" and r["evidence"][0]["word"] == "draft", r
    assert one("Draftsman", p)["status"] == "fail"


@case
def invalid_word_lists_rejected():
    for k in ("banned_words", "review"):
        f = write(f"prof_bad_{k}.json", {"extends": "default", "text": {k: "TODO"}})
        try:
            profiles.load(f)
        except ReelError as e:
            assert e.code == "PROFILE_INVALID", e.code
        else:
            raise AssertionError(f"{k} 不是数组却被接受")


@case
def not_checked_without_input():
    assert textscan.scan([], profiles.load())["status"] == "not_checked"


@case
def every_evidence_entry_has_severity():
    p = profiles.load()
    r = textscan.scan([("text", os.path.join(TMP, "missing.txt"), None)], p)
    assert r["evidence"] and all(e.get("severity") == "fail" for e in r["evidence"]), r
    p2 = dict(p, text=dict(p["text"], require=["brief"]))
    r = textscan.scan([], p2)
    assert r["status"] == "fail" and all(e.get("severity") == "fail" for e in r["evidence"]), r
    f = write("sev.txt", "TODO\nplaceholder\n")
    r = textscan.scan(textscan.collect(texts=[f]), p)
    assert {e["severity"] for e in r["evidence"]} == {"fail", "review"}, r


@case
def check_reports_needs_review():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "skip: 没有 ffmpeg"
    clip = os.path.join(TMP, "good.mp4")
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=8",
                    "-f", "lavfi", "-i", "sine=f=440:d=8:sample_rate=48000", "-af", "volume=2.44",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-t", "8", clip],
                   check=True)
    env = {**os.environ, "REEL_STATE_DIR": os.path.join(TMP, "state")}

    def run(txt):
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "reel.py"), "check", clip, "--text", txt],
                           capture_output=True, text=True, env=env)
        return json.loads(p.stdout), p.returncode
    out, code = run(write("ok.txt", "第一句口播。\n"))
    assert code == 0 and out["ok"], (code, out.get("error"))
    out, code = run(write("ph.txt", "封面标题：placeholder\n"))
    assert code == 1 and not out["ok"], (code, out.get("ok"))
    assert out["error"]["code"] == "CHECK_NEEDS_REVIEW", out["error"]
    assert "text_scan" in json.dumps(out["error"], ensure_ascii=False), out["error"]


if __name__ == "__main__":
    fails = 0
    for fn in CASES:
        try:
            note = fn()
            print(("skip " if note else "ok   ") + fn.__name__ + (f" ({note})" if note else ""))
        except Exception:  # noqa: BLE001
            fails += 1
            print("FAIL " + fn.__name__ + "\n" + traceback.format_exc(limit=4))
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"{len(CASES) - fails}/{len(CASES)} 通过")
    sys.exit(1 if fails else 0)
