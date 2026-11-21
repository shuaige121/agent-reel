#!/usr/bin/env python3
"""reel CLI 回归测试（无需 pytest）：python3 -B tests/test_cli.py
reel doctor、bin/reel 启动脚本、参数错误的 JSON、voice check 的 pypinyin 预检、核心套件在没有 Playwright 时跳过 web 用例。
全部用子进程跑；缺依赖用 PYTHONPATH 指向临时目录里 raise ImportError 的假包来模拟。只写自己的临时目录。"""
import json, os, re, shutil, site, subprocess, sys, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REEL = os.path.join(ROOT, "reel.py")
SHIM = os.path.join(ROOT, "bin", "reel")
TMP = tempfile.mkdtemp(prefix="reel_cli_test_")
RESULTS = []
REQUIRED = ["python", "ffmpeg", "ffprobe", "pillow", "numpy"]
OPTIONAL = ["subtitles", "playwright", "whisper", "pypinyin", "beats", "locate", "remote"]


def case(fn):
    try:
        fn(); RESULTS.append((fn.__name__, True, ""))
    except Exception as e:  # noqa: BLE001
        RESULTS.append((fn.__name__, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"))
    return fn


def _env(over):
    e = dict(os.environ)
    e["PYTHONDONTWRITEBYTECODE"] = "1"                 # 子进程（含经 bin/reel 启动的）不往仓库里写 __pycache__
    e.setdefault("PYTHONUSERBASE", site.getuserbase())  # 改 HOME 时用户 site-packages 不跟着丢
    for k, v in over.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


def run(argv, cwd=None, timeout=300, **over):
    return subprocess.run(argv, capture_output=True, text=True, cwd=cwd or TMP, env=_env(over), timeout=timeout)


def reel(*args, **over):
    return run([sys.executable, "-B", REEL, *args], **over)


def one_json(p):
    out = p.stdout.strip()
    assert out and "\n" not in out, f"stdout 不是单个 JSON：{p.stdout[-600:]!r} stderr={p.stderr[-600:]!r}"
    return json.loads(out)


def mkdir(*parts):
    d = os.path.join(TMP, *parts)
    os.makedirs(d, exist_ok=True)
    return d


def script(path, body):
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)
    return path


def fake_pkgs(tag, *names):
    """PYTHONPATH 目录：每个包的 __init__ 直接 raise ImportError（模拟没装）。"""
    d = mkdir("fake-" + tag)
    for n in names:
        os.makedirs(os.path.join(d, n), exist_ok=True)
        with open(os.path.join(d, n, "__init__.py"), "w") as fh:
            fh.write(f"raise ImportError('simulated: {n} is not installed')\n")
    old = os.environ.get("PYTHONPATH")
    return d + (os.pathsep + old if old else "")


def checks_of(r):
    res = r.get("result") or {}
    assert isinstance(res.get("checks"), list), r
    return res, {c["name"]: c for c in res["checks"]}


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


# ---------- reel doctor ----------
@case
def doctor_ok_on_this_machine():
    p = reel("doctor")
    r = one_json(p)
    assert p.returncode == 0 and r["ok"] is True, (p.returncode, r)
    res, by = checks_of(r)
    assert os.path.realpath(res["python"]) == os.path.realpath(sys.executable), res["python"]
    for c in res["checks"]:
        assert {"name", "ok", "required", "detail", "fix"} <= set(c), c
        assert (c["fix"] is None) == bool(c["ok"]), c
    for n in REQUIRED:
        assert n in by and by[n]["ok"] is True and by[n]["required"] is True, by.get(n)
    for n in OPTIONAL:
        assert n in by and by[n]["required"] is False, by.get(n)


@case
def doctor_missing_pillow_exits_1():
    p = reel("doctor", PYTHONPATH=fake_pkgs("pil", "PIL"))
    r = one_json(p)
    assert p.returncode == 1 and r["ok"] is False, (p.returncode, r)
    assert r["error"]["code"] == "DEPENDENCY_MISSING", r["error"]
    assert r["error"]["detail"]["missing"] == ["pillow"], r["error"]["detail"]
    _, by = checks_of(r)
    assert by["pillow"]["ok"] is False and by["pillow"]["fix"], by["pillow"]
    assert "~/.local/share/reel/venv" in by["pillow"]["fix"] and "pillow" in by["pillow"]["fix"], by["pillow"]
    assert by["numpy"]["ok"] is True, by["numpy"]


@case
def doctor_missing_ffmpeg_exits_1():
    empty = mkdir("empty-path")
    p = reel("doctor", PATH=empty)
    r = one_json(p)
    assert p.returncode == 1 and r["error"]["code"] == "DEPENDENCY_MISSING", (p.returncode, r.get("error"))
    missing = r["error"]["detail"]["missing"]
    assert {"ffmpeg", "ffprobe"} <= set(missing), missing
    _, by = checks_of(r)
    assert by["ffmpeg"]["fix"] and by["ffprobe"]["fix"] and by["subtitles"]["ok"] is False, by
    # ffmpeg、Pillow、NumPy 一起缺：doctor 照常出 JSON，不变成 INTERNAL_ERROR
    p = reel("doctor", PATH=empty, PYTHONPATH=fake_pkgs("pil-np", "PIL", "numpy"))
    r = one_json(p)
    assert p.returncode == 1 and r["error"]["code"] == "DEPENDENCY_MISSING", (p.returncode, r.get("error"))
    assert {"ffmpeg", "ffprobe", "pillow", "numpy"} <= set(r["error"]["detail"]["missing"]), r["error"]["detail"]


@case
def doctor_optional_missing_is_not_fatal():
    p = reel("doctor", PYTHONPATH=fake_pkgs("pinyin", "pypinyin"))
    r = one_json(p)
    assert p.returncode == 0 and r["ok"] is True, (p.returncode, r)
    _, by = checks_of(r)
    c = by["pypinyin"]
    assert c["ok"] is False and c["required"] is False and "pip install pypinyin" in (c["fix"] or ""), c


# ---------- bin/reel ----------
@case
def shim_runs_and_picks_interpreter():
    assert os.path.isfile(SHIM) and os.access(SHIM, os.X_OK), "bin/reel 不存在或不可执行"
    with open(SHIM) as fh:
        assert fh.readline().strip() == "#!/bin/bash"
    empty_home = mkdir("home-empty")
    p = run([SHIM, "--help"], HOME=empty_home, REEL_PYTHON=None)
    assert p.returncode == 0 and "usage:" in p.stdout, (p.returncode, p.stdout[-300:], p.stderr[-300:])
    # 1) REEL_PYTHON 非空 → 原样使用；参数、退出码、stdout/stderr 透传
    fake = script(os.path.join(mkdir("fake-py"), "py"),
                  'echo FAKE-REEL-PYTHON\nprintf "%s\\n" "$@"\necho to-stderr >&2\nexit 7\n')
    venv_home = mkdir("home-venv")
    vpy = os.path.join(mkdir("home-venv", ".local", "share", "reel", "venv", "bin"), "python")
    script(vpy, 'echo VENV-PYTHON\nprintf "%s\\n" "$@"\n')
    p = run([SHIM, "doctor", "a b", ""], HOME=venv_home, REEL_PYTHON=fake)
    lines = p.stdout.split("\n")
    assert p.returncode == 7 and lines[0] == "FAKE-REEL-PYTHON" and p.stderr.strip() == "to-stderr", (p.returncode, p.stdout, p.stderr)
    assert os.path.realpath(lines[1]) == os.path.realpath(REEL) and lines[2:5] == ["doctor", "a b", ""], lines
    # 2) 没设 REEL_PYTHON（或为空）、HOME 下有 venv python → 用 venv
    for rp in (None, ""):
        p = run([SHIM, "inspect", "x"], HOME=venv_home, REEL_PYTHON=rp)
        assert p.returncode == 0 and p.stdout.split("\n")[0] == "VENV-PYTHON", (rp, p.stdout, p.stderr)
    # 3) 两者都没有 → PATH 里的 python3（doctor 的 python 字段可核对）
    pbin = mkdir("path-python3")
    os.symlink(sys.executable, os.path.join(pbin, "python3"))
    p = run([SHIM, "doctor"], HOME=empty_home, REEL_PYTHON=None, PATH=pbin + os.pathsep + os.environ.get("PATH", ""))
    r = one_json(p)
    assert os.path.realpath(r["result"]["python"]) == os.path.realpath(sys.executable), r["result"]["python"]
    mbin = mkdir("path-marker")
    script(os.path.join(mbin, "python3"), 'echo PATH-PYTHON3\n')
    p = run([SHIM, "doctor"], HOME=empty_home, REEL_PYTHON=None, PATH=mbin + os.pathsep + os.environ.get("PATH", ""))
    assert p.returncode == 0 and p.stdout.strip() == "PATH-PYTHON3", (p.stdout, p.stderr)
    # 4) 经符号链接（相对链接套绝对链接）从别的目录调用，引擎根目录仍是 bin 的上一级
    d1, d2, elsewhere = mkdir("links", "a"), mkdir("links", "b"), mkdir("elsewhere")
    os.symlink(SHIM, os.path.join(d2, "reel-abs"))
    os.symlink(os.path.join("..", "b", "reel-abs"), os.path.join(d1, "reel"))
    p = run([os.path.join(d1, "reel"), "doctor"], cwd=elsewhere, REEL_PYTHON=fake)
    lines = p.stdout.split("\n")
    assert p.returncode == 7 and os.path.realpath(lines[1]) == os.path.realpath(REEL), (p.stdout, p.stderr)
    p = run(["reel", "--help"], cwd=elsewhere, HOME=empty_home, REEL_PYTHON=None,
            PATH=d1 + os.pathsep + os.environ.get("PATH", ""))
    assert p.returncode == 0 and "usage:" in p.stdout, (p.returncode, p.stdout[-300:], p.stderr[-300:])


# ---------- 参数错误 ----------
@case
def arg_errors_are_json():
    for args in ([], ["render"], ["nosuchcmd"], ["render", "plan.json", "--out", "o.mp4", "--where", "nowhere"]):
        p = reel(*args)
        r = one_json(p)
        assert p.returncode == 2, (args, p.returncode, r)
        e = r["error"]
        assert r["ok"] is False and e["code"] == "ARG_INVALID" and e["message"], (args, r)
        assert e["detail"]["usage"].startswith("usage: reel") and "\x1b" not in p.stdout, (args, e)
        assert "error: " + e["message"] in p.stderr, (args, p.stderr[-300:])     # 给人看的用法和报错仍在 stderr
    r = one_json(reel("render", "plan.json", "--out", "o.mp4", "--where", "nowhere"))
    assert "nowhere" in r["error"]["message"] and "reel render" in r["error"]["detail"]["usage"], r
    for args in (["--help"], ["-h"], ["render", "--help"]):
        p = reel(*args)
        assert p.returncode == 0 and p.stdout.lstrip().startswith("usage: reel"), (args, p.returncode, p.stdout[:200])


# ---------- voice check：中文先确认 pypinyin，再调 whisper ----------
@case
def voice_check_needs_pypinyin_before_whisper():
    d = mkdir("voice")
    marker = os.path.join(d, "whisper-called")
    wbin = mkdir("voice", "bin")
    script(os.path.join(wbin, "whisper-cli"), f'echo called >> "{marker}"\nexit 1\n')
    model = os.path.join(d, "model.bin")
    open(model, "w").close()
    sample = os.path.join(d, "sample.wav")
    ff("-f", "lavfi", "-i", "sine=f=440:d=1", "-ar", "16000", sample)
    zh, en = os.path.join(d, "zh.txt"), os.path.join(d, "en.txt")
    with open(zh, "w", encoding="utf-8") as fh:
        fh.write("这是一段用来测试试听核对流程的中文文本，至少需要二十个汉字才行。")
    with open(en, "w", encoding="utf-8") as fh:
        fh.write("This is a short English script for the voice check test.")
    env = {"PATH": wbin + os.pathsep + os.environ.get("PATH", ""), "REEL_WHISPER_MODEL": model,
           "PYTHONPATH": fake_pkgs("voice", "pypinyin")}
    p = reel("voice", "check", sample, "--script", zh, "--voice-id", "v1", **env)
    r = one_json(p)
    assert p.returncode == 1 and r["error"]["code"] == "BACKEND_UNAVAILABLE", (p.returncode, r)
    assert r["error"]["detail"]["missing"] == ["pypinyin"], r["error"]["detail"]
    assert r["error"]["detail"]["fix"].endswith("-m pip install pypinyin"), r["error"]["detail"]
    assert not os.path.exists(marker), "缺 pypinyin 时仍先调用了 whisper"
    p = reel("voice", "check", sample, "--script", en, "--voice-id", "v1", "--lang", "en", **env)
    r = one_json(p)
    assert os.path.exists(marker), ("--lang en 不需要 pypinyin，应调用 whisper", r)
    assert r["error"]["code"] == "WHISPER_FAILED", r["error"]


# ---------- 核心套件：没有 Playwright 时 web 用例记为跳过 ----------
@case
def core_suite_skips_web_without_playwright():
    p = run([sys.executable, "-B", os.path.join(ROOT, "tests", "test_reelcore.py")], timeout=1200,
            PYTHONPATH=fake_pkgs("pw", "playwright"))
    tail = (p.stdout + p.stderr)[-1500:]
    assert p.returncode == 0, tail
    assert re.search(r"^⏭ web_find_matches_dom_exactly — skipped: \S", p.stdout, re.M), tail
    last = p.stdout.strip().splitlines()[-1]
    assert re.fullmatch(r"(\d+)/\1 通过，1 跳过", last), last


if __name__ == "__main__":
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, msg in RESULTS:
        print(("✅ " if ok else "❌ ") + name + ("" if ok else "\n   " + msg.replace("\n", "\n   ")))
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if fails else 0)
