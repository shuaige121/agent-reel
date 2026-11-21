#!/usr/bin/env python3
"""MV 契约：合集 / trim 的逐首 song_level、song_identity 的尾窗、歌词重新导入失败的报错（无需 pytest）。

python3 -B tests/test_mvcheck.py
合成素材（正弦 + 噪声的「歌」，AAC 重编码的「成片」），直接调用 mvcheck.load / song_level / song_identity / run，
另有 reel check --contract 和 reel lyrics import 的端到端用例。只写 tempfile 目录，结束时清理。
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, traceback
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from reelcore import check, mvcheck, profiles  # noqa: E402
from reelcore.errors import ReelError  # noqa: E402

REEL = os.path.join(ROOT, "reel.py")
TMP = tempfile.mkdtemp(prefix="reel_mvcheck_")
STATE = os.path.join(TMP, "state")
SRF = 48000
W, H, FPS = 180, 320, 30


def P(n):
    return os.path.join(TMP, n)


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def j(p, obj=None):
    if obj is None:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    return p


def reel(*argv):
    env = {**os.environ, "REEL_STATE_DIR": STATE, "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-B", REEL, *map(str, argv)], capture_output=True, text=True, env=env)
    return json.loads(p.stdout), p.returncode


# ---------------- fixtures ----------------
def music(dur, f, amp, seed, mod=0.5):
    """一首合成「歌」：带慢速包络的正弦 + 低通噪声，立体声 float32，48 kHz。
    噪声先低通：白噪声在 AAC 截止频率以上的能量会被编码器切掉，响度差 -0.3 LU，离 0.5 LU 容差太近；真歌那里几乎没能量。"""
    rng = np.random.default_rng(seed)
    n = int(round(dur * SRF)); t = np.arange(n) / SRF
    noise = np.convolve(rng.standard_normal(n), np.ones(8) / np.sqrt(8), "same")
    m = amp * np.sin(2 * np.pi * f * t) * (0.6 + 0.4 * np.sin(2 * np.pi * mod * t)) + 0.2 * amp * noise
    return np.stack([m, np.roll(m, 37)], 1).astype(np.float32)


def _enc(a, out, pre=(), codec=("-c:a", "aac", "-b:a", "256k")):
    cmd = ["ffmpeg", "-y", "-v", "error", *pre, "-f", "f32le", "-ar", str(SRF), "-ac", "2", "-i", "-"]
    if pre:
        cmd += ["-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest"]
    subprocess.run(cmd + [*codec, "-ar", str(SRF), out], input=np.ascontiguousarray(a, "<f4").tobytes(), check=True)
    return out


def wav(name, a):
    """原曲：无损 wav。"""
    return _enc(a, P(name), codec=("-c:a", "pcm_f32le"))


def m4a(name, a):
    """只有音轨的「成片」（直接调用 song_level / song_identity 用）：AAC 重编码。"""
    return _enc(a, P(name))


def mp4(name, a):
    """带画面的成片（reel check 端到端用）：testsrc2 + AAC。"""
    d = len(a) / SRF
    return _enc(a, P(name), pre=("-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d={d}"))


def contract(name, songs):
    """songs = [(path, at, extra)]；extra 为空 → policy full。"""
    c = {"version": "reel.contract.v1", "kind": "mv", "output": {"width": W, "height": H, "fps": FPS},
         "songs": [{"path": p, "sha256": sha(p), "at": at, "policy": "full", **(x or {})} for p, at, x in songs]}
    return j(P(name), c)


def db(x):
    return np.float32(10 ** (x / 20))


A = music(10.0, 220, 0.25, 1)                 # 合集第一首
B = music(8.0, 330, 0.20, 2, mod=0.3)         # 合集第二首
Q = music(10.0, 260, 0.025, 3, mod=0.4)       # 安静的歌（约 -35 LUFS，default 的 loudness 会给 needs_review）
Q2 = music(6.0, 440, 0.025, 4, mod=0.7)       # 不在契约里的另一段，同样安静
T = music(10.5, 200, 0.25, 5, mod=0.6)        # 10.5 s：最后一窗只有 0.5 s
TRIM = {"policy": "trim", "range": [2, 8], "reason": "用户：只用第 2–8 秒"}


def setup():
    os.makedirs(STATE, exist_ok=True)
    wav("A.wav", A); wav("B.wav", B); wav("Q.wav", Q); wav("T.wav", T)
    mp4("medley.mp4", np.concatenate([A, B]))
    m4a("medley_b_quiet.m4a", np.concatenate([A, B * db(-1.5)]))
    m4a("trim.m4a", Q[2 * SRF:8 * SRF])
    m4a("trim_loud.m4a", Q[2 * SRF:8 * SRF] * db(2.0))
    m4a("trim_half.m4a", np.concatenate([Q[2 * SRF:8 * SRF], Q2]))
    m4a("single.m4a", A)
    m4a("tail_ok.m4a", T)
    muted = T.copy(); muted[int(10.0 * SRF):] = 0
    m4a("tail_muted.m4a", muted)
    contract("c_medley.json", [(P("A.wav"), 0.0, None), (P("B.wav"), 10.0, None)])
    contract("c_trim.json", [(P("Q.wav"), 0.0, TRIM)])
    contract("c_single.json", [(P("A.wav"), 0.0, None)])
    contract("c_tail.json", [(P("T.wav"), 0.0, None)])


def load(name):
    return mvcheck.load(P(name))


def run_items(video, con):
    """mvcheck.run 在真实的 default loudness / audio_clipping 结果上跑一遍（与 check.run 里同一个调用）。"""
    info, dur = check._probe(video)
    items = check.audio_items(video, profiles.load())
    mvcheck.run(video, con, info, dur, FPS, items)
    return {it["check"]: it for it in items}


# ---------------- song_level：合集 ----------------
def two_song_medley_level_passes():
    it = mvcheck.song_level(P("medley.mp4"), load("c_medley.json"))
    assert it["status"] == "pass", it
    songs = it["measured"]["songs"]
    assert [s["song"] for s in songs] == ["A.wav", "B.wav"] and [s["t"] for s in songs] == [[0.0, 10.0], [10.0, 18.0]], songs
    for s in songs:
        assert set(s) == {"song", "at", "t", "mv_lufs", "song_lufs", "mv_peak_dbfs", "song_peak_dbfs"}, s
        assert abs(s["mv_lufs"] - s["song_lufs"]) <= mvcheck.LOUD_SLACK_LU, s
    rp = P("medley.rep.json")
    d, rc = reel("check", P("medley.mp4"), "--contract", P("c_medley.json"), "--report", rp)
    r = j(rp); r = r.get("result", r)
    blockers = [b["check"] for b in r["gate"]["blockers"]]
    lv = next(x for x in r["items"] if x["check"] == "song_level")
    assert lv["status"] == "pass" and "song_level" not in blockers, (lv, blockers)
    assert next(x for x in r["items"] if x["check"] == "song_identity")["status"] == "pass"
    return "lufs diff " + ", ".join(f"{s['mv_lufs'] - s['song_lufs']:+.2f}" for s in songs) + f"; reel check blockers={blockers}"


def medley_second_song_gain_fails():
    it = mvcheck.song_level(P("medley_b_quiet.m4a"), load("c_medley.json"))
    assert it["status"] == "fail", it
    ev = [e for e in it["evidence"] if e.get("offending")]
    assert len(ev) == 1 and ev[0]["song"] == "B.wav" and ev[0]["t"] == [10.0, 18.0], ev
    assert set(ev[0]) >= {"song", "t", "lufs_diff", "peak_over_db", "offending"} and ev[0]["lufs_diff"] <= -1.0, ev
    return f"evidence → {ev[0]}"


# ---------------- song_level：trim ----------------
def trim_range_level_passes():
    con = load("c_trim.json")
    it = mvcheck.song_level(P("trim.m4a"), con)
    assert it["status"] == "pass", it
    s = it["measured"]["songs"][0]
    assert s["t"] == [0.0, 6.0] and abs(s["mv_lufs"] - s["song_lufs"]) <= mvcheck.LOUD_SLACK_LU, s
    assert mvcheck.song_identity(P("trim.m4a"), con)["status"] == "pass"
    # 区间覆盖成片全长 → default 的 loudness needs_review 被契约免除（与单曲完整路径同一个规则）
    items = run_items(P("trim.m4a"), con)
    lo = items["loudness"]
    assert lo["status"] == "pass" and lo.get("excused", {}).get("from") == "needs_review", lo
    return f"lufs {s['mv_lufs']} vs {s['song_lufs']}; loudness excused"


def trim_range_gain_fails():
    it = mvcheck.song_level(P("trim_loud.m4a"), load("c_trim.json"))
    assert it["status"] == "fail", it
    ev = it["evidence"][0]
    assert ev["song"] == "Q.wav" and ev["t"] == [0.0, 6.0] and ev["lufs_diff"] >= 1.5 and ev["peak_over_db"] > 1.0, ev
    return f"evidence → {ev}"


def trim_range_too_short_fails():
    """成片比契约区间短太多：可量区间 < 1 s → 该首 fail，evidence 写明原因（不是 not_checked）。"""
    c = j(P("c_trim.json")); c["songs"][0]["at"] = 5.5; j(P("c_trim_late.json"), c)
    it = mvcheck.song_level(P("trim.m4a"), load("c_trim_late.json"))
    assert it["status"] == "fail" and "why" in it["evidence"][0] and "1 s" in it["evidence"][0]["why"], it
    return it["evidence"][0]["why"]


# ---------------- song_level：单曲完整路径不变 ----------------
def single_full_song_unchanged():
    it = mvcheck.song_level(P("single.m4a"), load("c_single.json"))
    assert it["status"] == "pass" and set(it["measured"]) == {"mv_lufs", "song_lufs", "mv_peak_dbfs", "song_peak_dbfs"}, it
    return f"measured keys {sorted(it['measured'])}"


def partial_cover_keeps_loudness_verdict():
    con = load("c_trim.json")
    it = mvcheck.song_level(P("trim_half.m4a"), con)
    assert it["status"] == "pass", it   # 契约里这一首本身没问题
    items = run_items(P("trim_half.m4a"), con)
    lo, cl = items["loudness"], items["audio_clipping"]
    assert lo["status"] == "needs_review", lo   # 前提：确实有可以被免除的判定
    assert "excused" not in lo and "excused" not in cl, (lo, cl)
    assert items["song_level"]["status"] == "pass" and "6.00/12.00" in items["song_level"].get("note", ""), items["song_level"]
    return items["song_level"]["note"]


# ---------------- song_identity：尾窗 ----------------
def muted_tail_half_second_fails():
    it = mvcheck.song_identity(P("tail_muted.m4a"), load("c_tail.json"))
    assert it["status"] == "fail", it
    ev = [e for e in it["evidence"] if e.get("offending")]
    assert ev and all("t" in e and e["t"][0] >= 10.0 - 1e-6 and e["t"][1] <= 10.5 + 0.01 for e in ev), ev
    return f"evidence t={[e['t'] for e in ev]}"


def intact_tail_passes():
    it = mvcheck.song_identity(P("tail_ok.m4a"), load("c_tail.json"))
    assert it["status"] == "pass", it
    assert it["measured"]["songs"][0]["windows"] == 3, it["measured"]
    return "windows=3 (含 0.5 s 尾窗)"


# ---------------- lyrics import：重新导入失败 ----------------
LYR = "# t\n\n## Lyrics\n\n第一句歌词在这里\n第二句也要唱\n"
SRT_OK = "1\n00:00:01,000 --> 00:00:03,000\n第一句歌词在这里\n\n2\n00:00:03,500 --> 00:00:06,000\n第二句也要唱\n"
SRT_BAD = "1\n00:00:01,000 --> 00:00:03,000\n第一句歌词在这里\n"   # 漏了第二句 → coverage fail


def failed_reimport_reports_removal():
    open(P("x.md"), "w", encoding="utf-8").write(LYR)
    open(P("ok.srt"), "w", encoding="utf-8").write(SRT_OK)
    open(P("bad.srt"), "w", encoding="utf-8").write(SRT_BAD)
    out = P("x.lyrics.json")
    d, rc = reel("lyrics", "import", P("A.wav"), "--lyrics", P("x.md"), "--srt", P("ok.srt"), "--out", out)
    assert rc == 0 and d["result"]["status"] == "pass" and os.path.exists(out), d
    d, rc = reel("lyrics", "import", P("A.wav"), "--lyrics", P("x.md"), "--srt", P("bad.srt"), "--out", out)
    e = d.get("error") or {}
    assert rc == 1 and e.get("code") == "LYRICS_CHECK_FAILED", d
    assert e["detail"].get("removed_previous") is True and e["detail"].get("out") == os.path.abspath(out), e
    assert not os.path.exists(out) and "已删除" in e["message"] and out in e["message"], e
    first = P("y.lyrics.json")
    d, rc = reel("lyrics", "import", P("A.wav"), "--lyrics", P("x.md"), "--srt", P("bad.srt"), "--out", first)
    e = d.get("error") or {}
    assert rc == 1 and e.get("code") == "LYRICS_CHECK_FAILED" and e["detail"].get("removed_previous") is False, d
    assert "已删除" not in e["message"] and not os.path.exists(first), e
    return "重新导入失败 → removed_previous=true、文件已删、message 写明；首次失败 → false"


CASES = [two_song_medley_level_passes, medley_second_song_gain_fails, trim_range_level_passes, trim_range_gain_fails,
         trim_range_too_short_fails, single_full_song_unchanged, partial_cover_keeps_loudness_verdict,
         muted_tail_half_second_fails, intact_tail_passes, failed_reimport_reports_removal]


def main():
    try:
        setup()
    except Exception as e:  # noqa: BLE001
        print("fixture failed:", e); return 2
    results = []
    for fn in CASES:
        try:
            results.append((fn.__name__, True, fn() or ""))
        except (Exception, ReelError) as e:  # noqa: BLE001
            results.append((fn.__name__, False, f"{type(e).__name__}: {e} | {traceback.format_exc(limit=2).splitlines()[-3:]}"))
    bad = [r for r in results if not r[1]]
    for n, ok, note in results:
        print(f"{'PASS' if ok else 'FAIL'}  {n}  {str(note)[:400]}")
    print(f"{len(results) - len(bad)}/{len(results)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(rc)
