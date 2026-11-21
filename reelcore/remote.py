"""把一台远程 NVIDIA GPU 主机（例如 RTX 5090）当作 Mac 随时可调用的工具。

- 主机：REEL_REMOTE 环境变量（ssh 目标，如 user@gpu-host；可用空格分隔多个，按顺序选第一个在线的）。不设则不用远程。
- GPU 闸门：显存默认最多用到 80%；需要更多时报 NEED_USER_APPROVAL，由 Claude 先告诉用户
  （用户要先把显示器插到集显或切到无显示器 Ubuntu），得到同意后才能带 --allow-over-80 再跑。
- 同一时间只跑一个 GPU 任务：远程 flock 锁 ~/reel-remote/gpu.lock。
- 代码与素材用 rsync 推到 ~/reel-remote/jobs/<job>/，产物拉回本地后在 Mac 再验收一次（不信远程的「通过」）。
"""
import os, shutil, subprocess, tempfile, time, uuid
from .errors import ReelError

CANDIDATES = os.environ.get("REEL_REMOTE", "").split()
REMOTE_ROOT = "reel-remote"            # 相对远程 home
# 每台主机一个视觉环境（按 hostname 区分）：同一 home 被多个系统共用时，venv 不能混用
VENV = f"$HOME/{REMOTE_ROOT}/venv-$(hostname -s)"
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # plugins/reel
DEFAULT_VRAM_FRAC = 0.8
SSH_OPTS = ["-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15"]

_PROBE = r"""
set -e
echo "host=$(hostname)"; echo "user=$(whoami)"; echo "home=$HOME"
SMI=$(command -v nvidia-smi || echo /usr/lib/wsl/lib/nvidia-smi)
[ -x "$SMI" ] && echo "gpu=$($SMI --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits | head -1)"
echo "nvenc=$(ffmpeg -hide_banner -encoders 2>/dev/null | grep -c h264_nvenc || true)"
echo "libass=$(ffmpeg -hide_banner -filters 2>/dev/null | grep -cw ass || true)"
echo "python=$(python3 -c 'import sys;print(sys.version.split()[0])')"
V=$HOME/reel-remote/venv-$(hostname -s)/bin/python
[ -x $V ] && echo "vision=$($V -c 'import torch,transformers;print(torch.__version__,transformers.__version__,torch.cuda.is_available())' 2>/dev/null || echo broken)" || echo "vision=absent"
"""


def _ssh(host, script, timeout=120, check=True):
    p = subprocess.run(["ssh"] + SSH_OPTS + [host, "bash -s"], input=script, capture_output=True, text=True,
                       timeout=timeout)
    if check and p.returncode != 0:
        raise ReelError("REMOTE_FAILED", f"{host} 执行失败 (exit {p.returncode})", stderr=p.stderr[-1500:],
                        stdout=p.stdout[-1500:])
    return p


def probe(host=None):
    """探测 5090：在线主机、显存、NVENC、libass、视觉环境。离线返回 ok=False 而不是抛错（调用方可回退本机）。"""
    tried = []
    for h in ([host] if host else CANDIDATES):
        try:
            p = _ssh(h, _PROBE, timeout=25, check=False)
        except subprocess.TimeoutExpired:
            tried.append({"host": h, "error": "timeout"}); continue
        if p.returncode != 0:
            tried.append({"host": h, "error": p.stderr.strip()[-200:]}); continue
        kv = dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln)
        info = {"ok": True, "host": h, "hostname": kv.get("host"), "home": kv.get("home"),
                "nvenc": int(kv.get("nvenc", "0") or 0) > 0, "libass": int(kv.get("libass", "0") or 0) > 0,
                "python": kv.get("python"), "vision": kv.get("vision")}
        if kv.get("gpu"):
            name, used, total, util = [x.strip() for x in kv["gpu"].split(",")]
            info["gpu"] = {"name": name, "used_mib": int(used), "total_mib": int(total), "util_pct": int(util)}
        return info
    return {"ok": False, "tried": tried}


def gpu_gate(info, need_mib, allow_over=False, frac=DEFAULT_VRAM_FRAC):
    """开跑前检查：已用 + 本任务预计 ≤ 80%。超过则要求先告知用户；有人在用（利用率高）也不抢。"""
    g = info.get("gpu")
    if not g:
        raise ReelError("BACKEND_UNAVAILABLE", "远程没有可见的 GPU", host=info.get("host"))
    after = g["used_mib"] + need_mib
    cap = g["total_mib"] * frac
    if g["util_pct"] >= 50:
        raise ReelError("GPU_BUSY", "5090 当前 GPU 利用率高，可能有人在用，先告诉用户", **g)
    if after > cap and not allow_over:
        raise ReelError("NEED_USER_APPROVAL",
                        f"预计显存 {after}/{g['total_mib']} MiB 超过 {int(frac * 100)}%，需先告诉用户"
                        "（插集显或切无显示器 Ubuntu）并得到同意，再加 --allow-over-80", **g, need_mib=need_mib)
    return {"used_mib": g["used_mib"], "need_mib": need_mib, "cap_mib": int(cap)}


def _rsync(args, timeout=1800):
    p = subprocess.run(["rsync", "-a", "--partial", "-e", "ssh " + " ".join(SSH_OPTS)] + args,
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise ReelError("REMOTE_SYNC_FAILED", "rsync 失败", stderr=p.stderr[-800:], args=args[-2:])


def code_files():
    """reelcore 源码随每个 job 一起上传（job/code/reelcore），并发任务之间不会互相覆盖代码。"""
    root = os.path.join(PKG_DIR, "reelcore")
    return {f"code/reelcore/{n}": os.path.join(root, n) for n in sorted(os.listdir(root)) if n.endswith(".py")}


def new_job(host, files):
    """files: {远程相对路径: 本地路径}。本地先用符号链接摆好目录结构，再一次 rsync -L 推过去。返回远程 job 目录（相对 home）。"""
    job = f"{REMOTE_ROOT}/jobs/{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    stage = tempfile.mkdtemp(prefix="reel_stage_")
    try:
        for rel, local in files.items():
            dst = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(os.path.abspath(local), dst)
        _ssh(host, f"mkdir -p ~/{job}")
        _rsync(["-L", stage + "/", f"{host}:{job}/"])
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return job


def run_in_job(host, job, pycmd, gpu=True, venv=False, timeout=3600, need_mib=0, allow_over=False,
               frac=DEFAULT_VRAM_FRAC):
    """在 job 目录下执行 python 命令。gpu=True：持远程锁（同一时间只一个 reel GPU 任务），拿到锁后再查一次显存。"""
    py = f"{VENV}/bin/python" if venv else "python3"
    inner = f"cd ~/{job} && PYTHONPATH=~/{job}/code HF_HUB_DISABLE_XET=1 {py} {pycmd}"
    if gpu:
        recheck = "" if allow_over else (
            "SMI=$(command -v nvidia-smi || echo /usr/lib/wsl/lib/nvidia-smi); "
            "read U T G <<< $($SMI --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits"
            " | head -1 | tr -d ,); "
            "[ $G -lt 50 ] || { echo \"GPU_UTIL $G\" >&2; exit 77; }; "
            f"[ $((U + {int(need_mib)})) -le $((T * {int(frac * 100)} / 100)) ] || "
            "{ echo \"VRAM_OVER $U $T\" >&2; exit 76; }; ")
        inner = (f"exec 9>~/{REMOTE_ROOT}/gpu.lock; flock -w 600 9 || {{ echo GPU_LOCK_TIMEOUT >&2; exit 75; }}; "
                 + recheck + inner)
    p = _ssh(host, inner, timeout=timeout, check=False)
    if p.returncode == 75:
        raise ReelError("GPU_BUSY", "等 10 分钟仍拿不到 5090 GPU 锁（另一个 reel GPU 任务在跑）")
    if p.returncode == 77:
        raise ReelError("GPU_BUSY", "拿到锁后复查：GPU 利用率高，可能有人在用，先告诉用户", stderr=p.stderr[-200:])
    if p.returncode == 76:
        raise ReelError("NEED_USER_APPROVAL", "拿到锁后复查：显存将超过上限，需先告诉用户", stderr=p.stderr[-200:],
                        need_mib=need_mib)
    return p


def fetch(host, remote_rel, local_path):
    _rsync([f"{host}:{remote_rel}", local_path])


def setup_vision(host):
    """在 5090 建独立 venv（torch cu128 + transformers），不碰系统 Python 和其他项目环境。"""
    script = f"""
set -e
cd ~ && mkdir -p {REMOTE_ROOT}
if [ ! -x {VENV}/bin/python ]; then python3 -m venv {VENV}; fi
{VENV}/bin/pip install -q --upgrade pip
{VENV}/bin/pip install -q torch --index-url https://download.pytorch.org/whl/cu128
{VENV}/bin/pip install -q 'transformers>=4.45' pillow scipy
HF_HUB_DISABLE_XET=1 {VENV}/bin/python -c "from transformers import Owlv2Processor, Owlv2ForObjectDetection as M; \
Owlv2Processor.from_pretrained('google/owlv2-base-patch16-ensemble'); M.from_pretrained('google/owlv2-base-patch16-ensemble'); print('model ok')"
{VENV}/bin/python -c "import torch;print('cuda', torch.cuda.is_available(), torch.__version__)"
"""
    p = _ssh(host, script, timeout=3600)
    return p.stdout.strip().splitlines()[-2:]
