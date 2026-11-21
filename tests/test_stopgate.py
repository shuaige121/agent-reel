#!/usr/bin/env python3
"""完成闸门（Stop hook）：作用范围、同一 Bash 块里 deliver 之后再出片、回执要对上点名的作品。python3 -B tests/test_stopgate.py

hook 用 settings.json 里同样的 /usr/bin/python3 子进程跑（不存在则用当前解释器）；状态目录是临时的 REEL_STATE_DIR；
回执真跑 `reel deliver` 产生。
"""
import datetime, hashlib, json, os, shutil, subprocess, sys, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "hooks", "deliver_gate.py")
REEL = os.path.join(ROOT, "reel.py")
HOOK_PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
TMP = tempfile.mkdtemp(prefix="reel_stopgate_")
STATE = os.path.join(TMP, "state")
UNREL = os.path.join(TMP, "unrelated-project")         # 没有登记过的 cwd
HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
RESULTS = []


class Skip(Exception):
    pass


def case(fn):
    try:
        RESULTS.append((fn.__name__, "ok", fn() or ""))
    except Skip as e:
        RESULTS.append((fn.__name__, "skip", str(e)))
    except Exception as e:  # noqa: BLE001
        RESULTS.append((fn.__name__, "fail", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"))
    return fn


def reel(*args):
    env = {**os.environ, "REEL_STATE_DIR": STATE, "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "-B", REEL, *args], capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"reel {args[0]} exit {r.returncode}: {r.stdout[-800:]}{r.stderr[-800:]}"
    return r.stdout


def project(name):
    """建一个项目目录 + 交付目录 + brief，返回 brief 路径（还没登记）。"""
    proj, root = os.path.join(TMP, "projects", name), os.path.join(TMP, "delivery", name)
    os.makedirs(proj, exist_ok=True)
    os.makedirs(root, exist_ok=True)
    brief = os.path.join(proj, "brief.json")
    with open(brief, "w", encoding="utf-8") as fh:
        json.dump({"version": "reel.brief.v1", "name": name, "language": "zh-CN", "deliverable": "final",
                   "delivery_root": root, "must_not_change": [], "voice": {"mode": "none"}}, fh, ensure_ascii=False)
    return brief


# ---------- 状态目录：登记 promo-alpha / promo-beta / promo（被更长名包住）/ 频（单字，不算已知名）----------
for d in (STATE, UNREL):
    os.makedirs(d, exist_ok=True)
BRIEF = {n: project(n) for n in ("promo-alpha", "promo-beta", "promo", "频", "teaser-old")}
for n in ("promo-alpha", "promo-beta", "promo", "频"):
    reel("register", BRIEF[n])
PROJ_A, PROJ_B = os.path.dirname(BRIEF["promo-alpha"]), os.path.dirname(BRIEF["promo-beta"])

DELIVERED = {}       # (name, version) → reel deliver 的原始 stdout（就是 agent 在 Bash 输出里看到的）


def delivered(name, version):
    """真跑 reel deliver（合格片 + 有效审片记录）拿 final 回执；同一片子多次交付共用一个视频。"""
    if not HAVE_FF:
        raise Skip("没有 ffmpeg/ffprobe，跳过需要真实回执的用例")
    if (name, version) in DELIVERED:
        return DELIVERED[(name, version)]
    media = os.path.join(TMP, "media")
    v, rv, txt = os.path.join(media, "good.mp4"), os.path.join(media, "rv.json"), os.path.join(media, "t.txt")
    if not os.path.exists(v):
        os.makedirs(media, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=6",
                        "-f", "lavfi", "-i", "sine=f=440:d=6:sample_rate=48000", "-af", "volume=2.44",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-t", "6", v], check=True)
        with open(v, "rb") as fh:
            s = hashlib.sha256(fh.read()).hexdigest()
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write("口播文案。\n")
        with open(rv, "w", encoding="utf-8") as fh:
            json.dump({"records": [
                {"video_sha256": s, "check": "final_watch", "decision": "accept", "by": "claude", "note": "完整看完一遍",
                 "observations": [{"t": 0, "note": "首帧有画面"}]},
                {"video_sha256": s, "check": "final_listen", "decision": "accept", "by": "user", "note": "听完一遍",
                 "quote": "可以"}]}, fh, ensure_ascii=False)
    out = reel("deliver", v, "--brief", BRIEF[name], "--version", version, "--reviews", rv, "--text", txt)
    res = json.loads(out)["result"]
    assert res["status"] == "final" and res["name"] == name and res["label"] == version, res
    DELIVERED[(name, version)] = out
    return out


# ---------- transcript 与 hook 调用 ----------
def iso(dt):
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


T0 = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)
SEQ = [0]


def transcript(cmds, final, prompt="做个宣传视频"):
    """cmds: [(Bash 命令, 输出[, 块时间])]，按顺序各占一个 Bash 块；块时间默认 T0 + 序号秒。"""
    es = [{"type": "user", "uuid": "u0", "timestamp": iso(T0 - datetime.timedelta(hours=1)),
           "message": {"role": "user", "content": "早先的一轮"}},
          {"type": "user", "uuid": "u1", "timestamp": iso(T0), "message": {"role": "user", "content": prompt}}]
    for k, c in enumerate(cmds):
        cmd, res = c[0], c[1]
        ts = c[2] if len(c) > 2 else T0 + datetime.timedelta(seconds=k + 1)
        es.append({"type": "assistant", "timestamp": iso(ts),
                   "message": {"role": "assistant", "content": [
                       {"type": "tool_use", "id": f"t{k}", "name": "Bash", "input": {"command": cmd}}]}})
        es.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{k}", "content": res}]}})
    es.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": final}]}})
    SEQ[0] += 1
    p = os.path.join(TMP, f"t{SEQ[0]}.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(e, ensure_ascii=False) for e in es) + "\n")
    return p


def gate(cmds, final, cwd=UNREL, state=STATE, scope=None):
    """→ 拦截理由，放行 → None。scope=None 表示不设 REEL_GATE_SCOPE（默认范围）。"""
    env = {k: v for k, v in os.environ.items() if k != "REEL_GATE_SCOPE"}
    env["REEL_STATE_DIR"] = state
    if scope is not None:
        env["REEL_GATE_SCOPE"] = scope
    SEQ[0] += 1
    p = subprocess.run([HOOK_PY, GATE], input=json.dumps({"transcript_path": transcript(cmds, final), "cwd": cwd,
                                                          "last_assistant_message": final, "stop_hook_active": False,
                                                          "session_id": f"s{SEQ[0]}"}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["reason"] if p.stdout.strip() else None


def fresh_state(tag):
    d = os.path.join(TMP, "state-" + tag)
    os.makedirs(d, exist_ok=True)
    return d


FFMPEG = "ffmpeg -i in.mov -c:v libx264 out.mp4"
DONE_EN = "Done. Converted in.mov to out.mp4."
EXPORTED = "成片已导出好了"


# ---------- 1. 作用范围 ----------
@case
def unrelated_ffmpeg_passes_by_default():
    for tag, scope in (("default", None), ("empty", ""), ("other", "project")):
        st = fresh_state(tag)
        assert gate([(FFMPEG, "")], DONE_EN, state=st, scope=scope) is None, f"REEL_GATE_SCOPE={scope!r} 不该拦"
        assert not os.path.exists(os.path.join(st, "stopgate.json")), f"{scope!r}：放行时不该写 stopgate.json"
    assert gate([(FFMPEG, "")], DONE_EN, state=STATE) is None, "状态目录里有别的登记项目，cwd 不在其中也不该拦"
    why = gate([(FFMPEG, "")], DONE_EN, state=fresh_state("global"), scope="global")
    assert why and "reel deliver" in why, why
    return "未登记 cwd + 无关 ffmpeg +「Done」：默认/空值/其他取值 → 放行且不写 stopgate.json；REEL_GATE_SCOPE=global → 拦"


@case
def turn_that_called_reel_is_checked():
    st = fresh_state("called")
    blocked = [
        [("reel render plan.json --out out/v2.mp4", "")],
        [("python3 -B ~/.claude/plugins/reel/reel.py render plan.json --out out/v2.mp4", "")],
        [("bin/reel check out/v2.mp4 --brief brief.json", "{}"), ("ffmpeg -i a.mov -c:v libx264 out/v2.mp4", "")],
        [("reel inspect a.png", "{}"), ("ffmpeg -i a.mov -c:v libx264 out/v2.mp4", "")],
        [('reel() { python3 "$R/reel.py" "$@"; }\nREEL_X=1 reel render plan.json --out out/v2.mp4', "")],
    ]
    for cmds in blocked:
        assert gate(cmds, EXPORTED, state=st), f"本轮调用了 reel，应进入判断并拦：{cmds}"
    passed = [
        [('reel() { python3 "$R/reel.py" "$@"; }\npython3 build_video.py', "wrote out/final.mp4")],
        [("which reel; grep reel notes.txt", "reel"), ("ffmpeg -i a.mov -c:v libx264 out/v2.mp4", "")],
        [("python3 - <<'EOF'\nimport os\nos.system('reel render plan.json --out out/v2.mp4')\nEOF", ""),
         ("ffmpeg -i a.mov -c:v libx264 out/v2.mp4", "")],
    ]
    st2 = fresh_state("not-called")
    for cmds in passed:
        assert gate(cmds, EXPORTED, state=st2) is None, f"没有调用 reel，默认不该拦：{cmds}"
        assert gate(cmds, EXPORTED, state=fresh_state("not-called-g"), scope="global"), f"global 下应拦：{cmds}"
    assert not os.path.exists(os.path.join(st2, "stopgate.json"))
    return (f"reel render / python3 …/reel.py render / bin/reel check / reel inspect / 函数定义后真调用 → 拦 {len(blocked)} 例；"
            f"只有函数定义、which/grep、heredoc 里的 reel → 默认放行 {len(passed)} 例（global 下拦）")


@case
def registered_project_is_checked():
    msg = "v3 做好了，放在交付夹了。"
    cmds = [("ls out/", "final.mp4")]
    for cwd in (PROJ_B, os.path.join(PROJ_B, "out")):
        why = gate(cmds, msg, cwd=cwd)
        assert why and "登记过的 reel 项目" in why, why
    assert gate(cmds, msg, cwd=UNREL) is None
    return "cwd 在登记项目（或其子目录）里、无回执、说「v3 做好了」→ 拦；同样的话在未登记 cwd → 放行"


# ---------- 2. 同一 Bash 块里 deliver 之后再出片 ----------
DELIV_V1 = "reel deliver out/A.mp4 --brief brief.json --version v1 --reviews rv.json --text t.txt"
DELIV_V2 = "reel deliver out/A2.mp4 --brief brief.json --version v2 --reviews rv.json --text t.txt"
RENDER_B = "ffmpeg -i in.mov -c:v libx264 out/B.mp4"
PROMO = "宣传视频已完成，可以发了。"


@case
def production_after_deliver_in_same_block_is_seen():
    a1, a2 = delivered("promo-alpha", "v1"), delivered("promo-alpha", "v2")
    why = gate([(f"{DELIV_V1} && {RENDER_B}", a1)], PROMO)
    assert why and "没有对应的成片回执" in why, f"deliver 之后同块又出片，v1 回执不该背书：{why}"
    assert gate([(f"{DELIV_V1}; {RENDER_B}", a1)], "v1 已交付，可以发了。"), "分号连接同样算同一块"
    assert gate([(f"{RENDER_B} && {DELIV_V1}", a1)], PROMO) is None, "先出片后 deliver（同块）应放行"
    later = [(f"{DELIV_V1} && {RENDER_B}", a1), (DELIV_V2, a2)]
    assert gate(later, "v2 已交付，可以发了。") is None, "被污染的块之后重新 deliver v2，v2 回执照常认"
    assert gate(later, PROMO) is None
    why = gate(later, "v1 已交付，可以发了。")
    assert why and "v1" in why, f"v1 回执只出现在被污染的块里，不该认：{why}"
    # `python3 …/reel.py deliver` 片段本身不是出片：之后一块再 deliver（这里是失败的重复交付）不会让 v1 回执失效
    eng = "python3 ~/.claude/plugins/reel/reel.py deliver out/A.mp4 --brief brief.json --version v1"
    dup = json.dumps({"ok": False, "error": {"code": "VERSION_EXISTS", "message": "这个版本号已经交付过",
                                             "detail": {"target": "promo-alpha-v1.mp4"}}}, ensure_ascii=False)
    after = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=1)
    assert gate([(eng, a1), (eng, dup, after)], "v1 已交付，可以发了。") is None, "reel.py deliver 片段被当成了出片"
    return ("单块 deliver v1 && ffmpeg B.mp4 → 拦；单块 ffmpeg && deliver → 放行；之后另一块 deliver v2 →「v2 已交付」放行，"
            "「v1 已交付」仍拦；python3 …/reel.py deliver 本身不算出片")


ENG = "python3 -B ~/.claude/plugins/reel/reel.py"
V1_DONE = "v1 已交付，可以发了。"


@case
def register_after_production_does_not_hide_it():
    a1 = delivered("promo-alpha", "v1")
    for reg in ("reel register brief.json", f"{ENG} register brief.json", "bin/reel register brief.json"):
        why = gate([(f"{DELIV_V1} && {RENDER_B} && {reg}", a1)], PROMO)
        assert why and "没有对应的成片回执" in why, f"deliver → 出片 → register：块尾的 register 盖住了出片（{reg}）：{why}"
    assert gate([(f"{DELIV_V1} && reel register brief.json && {RENDER_B}", a1)], PROMO), "deliver → register → 出片 应拦"
    assert gate([(f"reel register brief.json && {RENDER_B} && {DELIV_V1}", a1)], PROMO) is None, "register → 出片 → deliver 应放行"
    return "块内先后只看最后一个 deliver：deliver → ffmpeg → register（三种写法）→ 拦；register → ffmpeg → deliver → 放行"


@case
def python_form_deliver_is_a_deliver():
    a1 = delivered("promo-alpha", "v1")
    forms = (f"{ENG} deliver out/A.mp4 --brief brief.json --version v1",
             '/usr/bin/python3 -B -u "$REEL_ENGINE_ROOT/reel.py" deliver out/A.mp4 --brief brief.json --version v1',
             "REEL_STATE_DIR=st python3 reel.py deliver out/A.mp4 --brief brief.json --version v1",
             "bin/reel deliver out/A.mp4 --brief brief.json --version v1",
             DELIV_V1)
    for d in forms:
        why = gate([(f"{d} && {RENDER_B}", a1)], PROMO)
        assert why and "没有对应的成片回执" in why, f"{d} 之后同块出片，v1 回执不该背书：{why}"
        assert gate([(f"{RENDER_B} && {d}", a1)], PROMO) is None, f"先出片后 deliver 应放行：{d}"
        assert gate([(d, a1)], V1_DONE) is None, f"只 deliver 应放行：{d}"
    return f"python3 [-B -u] …/reel.py deliver、bin/reel deliver、reel deliver 同样对待（{len(forms)} 种写法 × 出片在前/在后/无出片）"


@case
def readonly_reel_subcommands_are_not_production():
    a1 = delivered("promo-alpha", "v1")
    rcp = reel("receipt", json.loads(a1)["result"]["receipt"])          # 真实的 reel receipt 输出，含交付的 .mp4 路径
    assert ".mp4" in rcp, rcp
    after = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=1)
    ro = ["python3 reel.py receipt .reel/receipts/promo-alpha-v1.json", "bin/reel receipt .reel/receipts/promo-alpha-v1.json",
          f"{ENG} receipt .reel/receipts/promo-alpha-v1.json", f"{ENG} check out/A.mp4 --brief brief.json",
          f"{ENG} inspect out/A.mp4", f"{ENG} timeline plan.json --png out/tl.png", f"{ENG} scrub out/A.mp4 --at 1.0",
          f"{ENG} validate plan.json", f"{ENG} compile plan.json", f"{ENG} history plan.json", f"{ENG} frame out/A.mp4",
          f"{ENG} observe --obs o.json --src assets --asset a.png --bbox 0.1,0.1,0.5,0.5 --label t",
          f"{ENG} locate a.png --query cup", f"{ENG} words voice.wav", f"{ENG} voice check voice.wav",
          f"{ENG} lyrics verify lyrics.json", f"{ENG} mlt project.mlt", f"{ENG} beats song.mp3", f"{ENG} doctor",
          f"{ENG} remote probe"]
    for c in ro:
        assert gate([(f"{DELIV_V1} && {c}", a1 + "\n" + rcp)], V1_DONE) is None, f"同块 deliver 之后的只读子命令被当成出片：{c}"
        assert gate([(DELIV_V1, a1), (c, rcp, after)], V1_DONE) is None, f"之后一块的只读子命令被当成出片：{c}"
    for c in (f"{ENG} render plan.json --out out/B.mp4", "bin/reel preview plan.json --out out/B.mp4",
              "python3 build_reel.py --out out/B.mp4"):
        assert gate([(f"{DELIV_V1} && {c}", a1)], V1_DONE), f"deliver 之后同块出片应拦：{c}"
    return (f"reel 只读子命令（{len(ro)} 种：receipt/check/inspect/timeline/scrub/… 三种写法）在 deliver 之后（同块或下一块）"
            "不算出片 → 放行；reel.py render、bin/reel preview、自写 build_reel.py 仍算出片 → 拦")


# ---------- 3. 回执要对上点名的作品 ----------
MISSING = lambda *names: f"声称的作品 {list(names)} 没有对应的成片回执"     # noqa: E731  理由里列出的缺回执作品


@case
def receipt_must_match_named_work():
    a1 = delivered("promo-alpha", "v1")
    delivered("teaser-old", "v1")                        # 交付后撤销登记：名字只留在交付流水里
    reel("register", BRIEF["teaser-old"], "--remove")
    turn = [(DELIV_V1, a1)]
    why = gate(turn, "promo-beta v1 视频已完成。")
    assert why and MISSING("promo-beta") in why, f"只交付了 promo-alpha，声称 promo-beta 应拦：{why}"
    assert gate(turn, "promo-alpha v1 视频已完成。") is None
    assert gate(turn, "v1 已交付，可以发了。") is None
    why = gate(turn, "PROMO-BETA v1 视频已完成。")
    assert why and MISSING("promo-beta") in why, f"作品名不分大小写：{why}"
    why = gate(turn, "promo-alpha v1 和 promo-beta v1 视频都已完成。")
    assert why and MISSING("promo-beta") in why, f"两个作品里只有 promo-beta 缺回执：{why}"
    why = gate(turn, "promo-alpha v2 视频已完成。")
    assert why and MISSING("promo-alpha v2") in why, f"作品对上了、版本号对不上也要拦：{why}"
    why = gate(turn, "promo v1 视频已完成。")
    assert why and MISSING("promo") in why, f"「promo」是已知名，单独出现要有自己的回执：{why}"
    why = gate(turn, "teaser-old v1 视频已完成。")
    assert why and MISSING("teaser-old") in why, f"只在交付流水里的作品名也算已知名：{why}"
    # 登记项目模式（本轮没有产出，按项目取回执）同样适用
    look = [("ls out/", "promo-alpha-v1.mp4")]
    why = gate(look, "promo-beta v1 视频已完成。", cwd=PROJ_A)
    assert why and MISSING("promo-beta") in why, why
    assert gate(look, "promo-alpha v1 视频已完成。", cwd=PROJ_A) is None
    return ("只交付 promo-alpha v1：「promo-beta v1 视频已完成」→ 拦（理由含 promo-beta）；「promo-alpha v1」「v1 已交付」→ 放行；"
            "名字不分大小写、promo 被 promo-alpha 包住不算、单字名不算、交付流水里的名字算；登记项目模式同样")


if __name__ == "__main__":
    lines = []
    for n, st, m in RESULTS:
        mark = {"ok": "✅", "skip": "⏭️ ", "fail": "❌"}[st]
        lines.append(f"{mark} {n}" + (f" — {m}" if st != "fail" and m else ("\n   " + m.replace("\n", "\n   ") if st == "fail" else "")))
    fails = [r for r in RESULTS if r[1] == "fail"]
    ran = [r for r in RESULTS if r[1] != "skip"]
    skipped = len(RESULTS) - len(ran)
    lines.append(f"\n{len(ran) - len(fails)}/{len(ran)} 通过" + (f"，跳过 {skipped}" if skipped else "")
                 + f"（hook 解释器 {HOOK_PY}）")
    print("\n".join(lines))
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if fails else 0)
