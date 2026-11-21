#!/usr/bin/env python3
"""Range-preview acceptance tests (no pytest): python3 -B tests/test_preview_accept.py
`reel preview --range` must report ACCEPTANCE_FAILED (exit 1, top-level ok false) when its own verify check fails,
like `reel render`. Temp dirs only."""
import atexit, contextlib, io, json, os, shutil, subprocess, sys, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
TMP = tempfile.mkdtemp(prefix="reel_preview_accept_")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)  # removed on pass, fail, or exception
os.environ["REEL_STATE_DIR"] = os.path.join(TMP, "state")
RESULTS = []


def case(fn):
    try:
        fn(); RESULTS.append((fn.__name__, True, ""))
    except Exception as e:  # noqa: BLE001
        RESULTS.append((fn.__name__, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"))
    return fn


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def workdir(name):
    d = os.path.join(TMP, name)
    a = os.path.join(d, "assets")
    os.makedirs(a)
    ff("-f", "lavfi", "-i", "testsrc2=size=800x600", "-frames:v", "1", os.path.join(a, "a.png"))
    ff("-f", "lavfi", "-i", "sine=f=300:d=3", "-ar", "48000", os.path.join(a, "voice.wav"))
    plan = {"version": "reel.plan.v1", "src_dir": "assets", "output": {"width": 540, "height": 960, "fps": 30},
            "duration_policy": "fit_last_shot",
            "shots": [{"id": "s1", "asset": "a.png", "duration": 3.5, "camera": []}], "audio": {"voice": "voice.wav"}}
    json.dump(plan, open(os.path.join(d, "plan.json"), "w"))
    return d


def run_main(d, argv, patch=None):
    """In-process cli.main; returns (exit code, parsed stdout JSON). `patch` = (module, attr, replacement)."""
    from reelcore import cli
    old_cwd = os.getcwd()
    orig = getattr(patch[0], patch[1]) if patch else None
    buf = io.StringIO()
    try:
        os.chdir(d)
        if patch:
            setattr(patch[0], patch[1], patch[2](orig))
        with contextlib.redirect_stdout(buf):
            rc = cli.main(argv)
    finally:
        if patch:
            setattr(patch[0], patch[1], orig)
        os.chdir(old_cwd)
    return rc, json.loads(buf.getvalue())


@case
def passing_range_preview_exits_0():
    d = workdir("ok")
    rc, out = run_main(d, ["preview", "plan.json", "--range", "0:2", "--out", os.path.join(d, "pv.mp4")])
    assert rc == 0 and out["ok"] is True, (rc, out)
    assert out["result"]["ok"] is True, out


@case
def failing_acceptance_reports_acceptance_failed():
    from reelcore import verify

    def forced_44k(orig):  # real verify.check, but pretend the delivered file had expect 48000 / actual 44100
        def check(*a, **kw):
            res = orig(*a, **kw)
            for c in res["checks"]:
                if c["check"] == "audio_sample_rate":
                    c["ok"], c["actual"] = False, 44100
            res["ok"] = False
            return res
        return check

    d = workdir("bad")
    rc, out = run_main(d, ["preview", "plan.json", "--range", "0:2", "--out", os.path.join(d, "pv.mp4")],
                       patch=(verify, "check", forced_44k))
    assert rc == 1, (rc, out)
    assert out["ok"] is False and out["error"]["code"] == "ACCEPTANCE_FAILED", out
    bad = [c for c in out["error"]["detail"]["checks"] if not c["ok"]]
    assert any(c["check"] == "audio_sample_rate" and c["actual"] == 44100 for c in bad), bad


for name, ok, msg in RESULTS:
    print(("PASS " if ok else "FAIL ") + name + (("\n" + msg) if msg else ""))
failed = [n for n, ok, _ in RESULTS if not ok]
print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)
