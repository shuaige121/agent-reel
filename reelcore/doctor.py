"""reel doctor：列出必需与可选依赖，缺什么给出可直接执行的修复命令。

必需项（任何一项缺失 → DEPENDENCY_MISSING，退出 1）：python ≥ 3.9、ffmpeg、ffprobe、Pillow、NumPy。
可选项只报告：字幕烧录、Playwright、whisper、pypinyin、节拍 venv、本机检测（torch/transformers）、远程 GPU。
本模块只用标准库；缺 ffmpeg / Pillow / NumPy 时自身照常运行。不发起任何网络连接。
"""
import importlib, importlib.util, json, os, shlex, shutil, subprocess, sys
from .errors import GateFailed

VENV = "~/.local/share/reel/venv"
VENV_FIX = f"python3 -m venv {VENV} && {VENV}/bin/python -m pip install pillow numpy"
WHISPER_MODEL = "~/models/whisper/ggml-large-v3-turbo.bin"
WHISPER_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"

# 子进程里问 Playwright 自己：能否导入、Chromium 可执行文件在哪（只启动本地 driver，不联网）
_PW_PROBE = r"""
import json, os
try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    print(json.dumps({"import": "%s: %s" % (type(e).__name__, e)})); raise SystemExit(0)
with sync_playwright() as p:
    exe = p.chromium.executable_path
print(json.dumps({"exe": exe, "exists": bool(exe) and os.path.isfile(exe)}))
"""


def _py():
    return shlex.quote(sys.executable)


def _pkg_fix(name):
    if sys.platform == "darwin":
        return f"brew install {name}"
    if sys.platform.startswith("linux"):
        return f"sudo apt-get install -y {name}"
    return f"安装 {name} 并放进 PATH"


def _item(name, required, ok, detail, fix):
    return {"name": name, "ok": bool(ok), "required": required, "detail": detail, "fix": None if ok else fix}


def _import(mod):
    """真的导入一次；返回 (ok, 版本或错误说明)。"""
    try:
        m = importlib.import_module(mod)
    except Exception as e:  # noqa: BLE001  坏掉的安装也只算「不可用」，doctor 自己不能崩
        return False, f"{type(e).__name__}: {e}"
    return True, str(getattr(m, "__version__", "") or "已安装")


def _ffmpeg_filters():
    if not shutil.which("ffmpeg"):
        return set()
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {ln.split()[1] for ln in p.stdout.splitlines() if len(ln.split()) > 2 and ln.startswith(" ")}


# ---------------- 必需 ----------------

def _required():
    v = sys.version_info
    out = [_item("python", True, v >= (3, 9), f"{v[0]}.{v[1]}.{v[2]}（{sys.executable}）",
                 "需要 Python ≥ 3.9：装好新版 Python 后用它建 venv（python3 -m venv ~/.local/share/reel/venv），"
                 "或设 REEL_PYTHON 指向它")]
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        out.append(_item(tool, True, path, path or "PATH 里没有 " + tool, _pkg_fix("ffmpeg")))
    for name, mod in (("pillow", "PIL"), ("numpy", "numpy")):
        ok, detail = _import(mod)
        out.append(_item(name, True, ok, detail, VENV_FIX))
    return out


# ---------------- 可选 ----------------

def _subtitles():
    if "ass" in _ffmpeg_filters():
        return _item("subtitles", False, True, "ffmpeg 有 ass 滤镜（libass）", None)
    mpv = shutil.which("mpv")
    return _item("subtitles", False, mpv, f"mpv：{mpv}" if mpv else "ffmpeg 没有 ass 滤镜，PATH 里也没有 mpv",
                 _pkg_fix("mpv") + "  # 或换带 libass 的 ffmpeg")


def _playwright():
    py = _py()
    install = f"{py} -m pip install playwright && {py} -m playwright install chromium"
    try:
        p = subprocess.run([sys.executable, "-c", _PW_PROBE], capture_output=True, text=True, timeout=120)
        r = json.loads(p.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        return _item("playwright", False, False, f"探测失败：{type(e).__name__}: {e}", install)
    if "import" in r:
        return _item("playwright", False, False, "playwright 不可导入：" + r["import"], install)
    if not r.get("exists"):
        return _item("playwright", False, False, f"Chromium 不存在：{r.get('exe')}", f"{py} -m playwright install chromium")
    return _item("playwright", False, True, r["exe"], None)


def _whisper():
    cli = shutil.which("whisper-cli")
    env_model = os.environ.get("REEL_WHISPER_MODEL")
    model = env_model or os.path.expanduser(WHISPER_MODEL)
    has_model = os.path.isfile(model)
    if cli and has_model:
        return _item("whisper", False, True, f"{cli}；模型 {model}", None)
    fixes = []
    if not cli:
        fixes.append(_pkg_fix("whisper-cpp"))
    if not has_model:
        fixes.append(f"mkdir -p {shlex.quote(os.path.dirname(model) or '.')} && curl -L -o {shlex.quote(model)} "
                     f"{WHISPER_URL}")
    detail = "；".join(x for x in (None if cli else "PATH 里没有 whisper-cli",
                                   None if has_model else f"模型不存在：{model}") if x)
    note = "" if has_model else "  # 或把 REEL_WHISPER_MODEL 指向已有的 ggml 模型"
    return _item("whisper", False, False, detail, " && ".join(fixes) + note)


def _pypinyin():
    ok, detail = _import("pypinyin")
    return _item("pypinyin", False, ok, detail if ok else "中文 reel voice check 需要：" + detail,
                 f"{_py()} -m pip install pypinyin")


def _beats():
    venv_py = os.environ.get("REEL_VENV_PY") or os.path.expanduser(VENV + "/bin/python")
    ok = os.path.isfile(venv_py) and os.access(venv_py, os.X_OK)
    return _item("beats", False, ok, venv_py if ok else f"节拍分析的 python 不存在：{venv_py}",
                 f"python3 -m venv {VENV} && {VENV}/bin/python -m pip install librosa  # 或设 REEL_VENV_PY 指向已有的 python")


def _locate():
    found = {}
    for mod in ("torch", "transformers"):
        try:
            found[mod] = importlib.util.find_spec(mod) is not None      # 只找，不导入（torch 导入很慢）
        except (ImportError, ValueError):
            found[mod] = False
    missing = [m for m, ok in found.items() if not ok]
    return _item("locate", False, not missing, "torch、transformers 可找到" if not missing else "缺 " + "、".join(missing),
                 f"{_py()} -m pip install torch transformers  # 或设 REEL_REMOTE，用 reel locate --where 5090 走远程")


def _remote():
    hosts = os.environ.get("REEL_REMOTE", "").split()
    return _item("remote", False, hosts, "REEL_REMOTE：" + " ".join(hosts) if hosts else "未设 REEL_REMOTE（不用远程 GPU）",
                 "export REEL_REMOTE=user@gpu-host  # ssh 目标，空格分隔多个；不用远程 GPU 可以不设")


def run():
    checks = _required() + [_subtitles(), _playwright(), _whisper(), _pypinyin(), _beats(), _locate(), _remote()]
    result = {"python": sys.executable, "checks": checks}
    missing = [c["name"] for c in checks if c["required"] and not c["ok"]]
    if missing:
        raise GateFailed("DEPENDENCY_MISSING", "缺少必需依赖：" + ", ".join(missing) + "（各项 fix 是修复命令）", result,
                         missing=missing)
    return result
