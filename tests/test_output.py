#!/usr/bin/env python3
"""Output tests (no pytest): python3 tests/test_output.py
Render is always AAC 48 kHz whatever the input rates; delivered files follow the umask."""
import json, os, shutil, stat, subprocess, sys, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
TMP = tempfile.mkdtemp(prefix="reel_output_")
STATE = os.path.join(TMP, "state")
RESULTS = []
ENV = dict(os.environ, REEL_STATE_DIR=STATE)


def case(fn):
    try:
        r = fn()
        RESULTS.append((fn.__name__, True, "skip: " + r if isinstance(r, str) else ""))
    except Exception as e:  # noqa: BLE001
        RESULTS.append((fn.__name__, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"))
    return fn


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def reel(*args, cwd=None, env=None, check_rc=True):
    p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "reel.py"), *args], capture_output=True, text=True,
                       cwd=cwd, env=env or ENV)
    if check_rc:
        assert p.returncode == 0, f"reel {args} rc={p.returncode}\n{p.stdout[-800:]}\n{p.stderr[-800:]}"
    return p


def probe_audio(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=sample_rate,codec_name", "-of", "json", path], capture_output=True, text=True)
    s = json.loads(p.stdout)["streams"][0]
    return int(s["sample_rate"]), s["codec_name"]


def workdir(name, voice_ext, voice_ar, bgm_ar=None, subs=False):
    d = os.path.join(TMP, name)
    os.makedirs(os.path.join(d, "assets"))
    a = os.path.join(d, "assets")
    ff("-f", "lavfi", "-i", "testsrc2=size=800x600", "-frames:v", "1", os.path.join(a, "a.png"))
    ff("-f", "lavfi", "-i", "sine=f=300:d=3", "-ar", str(voice_ar), os.path.join(a, "voice" + voice_ext))
    au = {"voice": "voice" + voice_ext}
    if bgm_ar:
        ff("-f", "lavfi", "-i", "sine=f=120:d=2", "-ar", str(bgm_ar), os.path.join(a, "bgm.mp3"))
        au["bgm"] = "bgm.mp3"
    if subs:
        open(os.path.join(a, "v.vtt"), "w").write(
            "WEBVTT\n\n00:00.000 --> 00:01.500\nHello there.\n\n00:01.500 --> 00:02.800\nSecond line.\n")
        au["subtitles"] = "v.vtt"
    plan = {"version": "reel.plan.v1", "src_dir": "assets", "output": {"width": 540, "height": 960, "fps": 30},
            "duration_policy": "fit_last_shot",
            "shots": [{"id": "s1", "asset": "a.png", "duration": 3.5, "camera": []}], "audio": au}
    json.dump(plan, open(os.path.join(d, "plan.json"), "w"))
    return d


def render_ok(d):
    out = os.path.join(d, "out.mp4")
    r = json.loads(reel("render", "plan.json", "--out", out, "--where", "mac", cwd=d).stdout)
    res = r.get("result", r)
    assert res.get("ok") is True, r
    chk = [c for c in res["checks"] if c["check"] == "audio_sample_rate"]
    assert chk and chk[0]["ok"] and chk[0]["actual"] == 48000, res["checks"]
    assert probe_audio(out) == (48000, "aac"), probe_audio(out)


@case
def render_44k_mp3_voice_outputs_48k():
    render_ok(workdir("r44", ".mp3", 44100))


@case
def render_24k_voice_and_44k_bgm_outputs_48k():
    render_ok(workdir("r24", ".wav", 24000, bgm_ar=44100))


@case
def render_with_subtitles_outputs_48k():
    filt = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    if " ass " not in filt and not shutil.which("mpv"):
        return "no ffmpeg ass filter and no mpv"
    render_ok(workdir("rsub", ".mp3", 44100, subs=True))


@case
def preview_range_outputs_48k():
    d = workdir("pv", ".mp3", 44100)
    out = os.path.join(d, "pv.mp4")
    res = json.loads(reel("preview", "plan.json", "--range", "0:2", "--out", out, cwd=d).stdout)
    res = res.get("result", res)
    assert res.get("ok") is True, res
    assert probe_audio(out) == (48000, "aac"), probe_audio(out)


@case
def verify_reports_sample_rate_only_when_asked():
    from reelcore import verify
    d = os.path.join(TMP, "vr")
    os.makedirs(d)
    f = os.path.join(d, "v.mp4")
    ff("-f", "lavfi", "-i", "color=c=blue:s=320x240:r=30:d=1", "-f", "lavfi", "-i", "sine=f=300:d=1", "-ar", "44100",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", f)
    kw = dict(expect_s=1.0, fps=30, width=320, height=240, tol_s=0.3)
    r = verify.check(f, expect_sample_rate=48000, **kw)
    sr = [c for c in r["checks"] if c["check"] == "audio_sample_rate"]
    assert sr and not sr[0]["ok"] and sr[0]["actual"] == 44100 and r["ok"] is False, r
    r = verify.check(f, **kw)
    assert not [c for c in r["checks"] if c["check"] == "audio_sample_rate"], r


@case
def delivered_file_mode_follows_umask():
    d = workdir("dl", ".wav", 48000)
    out = os.path.join(d, "demo.mp4")
    reel("render", "plan.json", "--out", out, "--where", "mac", cwd=d)
    root = os.path.join(TMP, "delivered")
    os.makedirs(root)
    json.dump({"version": "reel.brief.v1", "name": "demo", "language": "en", "deliverable": "sample",
               "delivery_root": root, "must_not_change": [], "voice": {"mode": "recorded"}, "profile": "default"},
              open(os.path.join(d, "brief.json"), "w"))
    for i, (um, want) in enumerate([(0o022, 0o644), (0o077, 0o600), (0o002, 0o664)], 1):
        ver = f"v{i}"
        p = subprocess.run(["bash", "-c", f"umask {um:03o}; exec \"$0\" -B \"$1\" deliver demo.mp4 --brief brief.json "
                            f"--version {ver} --plan plan.json --sample", sys.executable, os.path.join(ROOT, "reel.py")],
                           capture_output=True, text=True, cwd=d, env=ENV)
        assert p.returncode == 0, p.stdout[-800:] + p.stderr[-800:]
        res = json.loads(p.stdout)["result"]
        files = [f for f in os.listdir(root) if f.endswith(".mp4") and ver in f]
        assert len(files) == 1, os.listdir(root)
        mode = stat.S_IMODE(os.stat(os.path.join(root, files[0])).st_mode)
        assert mode == want, f"umask {um:03o}: mode {mode:o}, want {want:o}"
        reel("receipt", res["receipt"], cwd=d)


def main():
    try:
        for n, ok, msg in RESULTS:
            print(("ok   " if ok else "FAIL ") + n + (f"  ({msg})" if ok and msg else ""))
            if not ok:
                print(msg)
        n_ok = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"{n_ok}/{len(RESULTS)} passed")
        return 0 if n_ok == len(RESULTS) else 1
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


sys.exit(main())
