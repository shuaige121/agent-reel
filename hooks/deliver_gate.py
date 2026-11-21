#!/usr/bin/env python3
"""Stop hook：本轮做了视频产出（或会话在登记过的 reel 项目里），回复却声称完成/交付，又拿不出对应的成片回执 → 拦。

- 作用范围（默认）：只管两类会话——会话 cwd 在 `reel register` 登记过的项目目录里；或本轮某个 Bash 命令调用了 reel
  （命令位置上的 `reel <子命令>`、`bin/reel …`、`python3 …/reel.py <子命令>`；函数定义 `reel() {…}`、`which reel`、
  `grep reel` 不算）。其余会话立即放行：不输出、不写 stopgate.json。
  环境变量 REEL_GATE_SCOPE=global → 对本机所有会话生效（旧行为）。范围只做收窄，进入判断后规则不变。
- 「视频产出」：命令位置上的 ffmpeg / melt / reel render|preview / build_*.py 一类脚本，命令或输出里有视频文件，
  不在临时/测试目录；或本轮调了 reel deliver / register；或会话 cwd 在登记任务的项目目录里。
  reel 的其他子命令（deliver / register / check / receipt / inspect …）不算出片，三种写法（reel、bin/reel、
  python3 [-B] …/reel.py）一样对待。
- 「声称完成」逐句判断：一句话里有完成措辞、谈的是片子（成片/视频/样片/交付/mp4/vN），且这句和下一句都没有
  如实状态（待审样片/未完成/待试听/未交付…）。只说测试/代码/文档完成的句子不算。全文写「本轮无视频交付」→ 不算。
- 认哪些回执：status=final、交付文件仍在且 sha256 没变，并且
  * 本轮有产出：回执路径出现在本轮命令输出里（即本轮自己 deliver 的），且晚于本轮最后一次产出命令；
    同一个 Bash 块里 reel deliver 之后又出片 → 路径只出现在这一块及更早块里的回执都不算（按块序，不看时间戳）；
  * 只是在登记项目里：该项目的回执（不限时间，用于回答「成片在哪」）。
  声称里点名的版本号（vN）都要有对应的 final 回执。声称里点名了已知作品（登记表 / 交付流水里的 name，至少 2 个字符，
  不分大小写；被更长的已知名包住的不算）→ 每个作品都要有同名的 final 回执，句中的版本号也要对上句中的作品。
- 同一轮最多拦 MAX_BLOCKS 次（防 hook 误判导致死循环），放行时记 stopgate.log；出错静默放行。
它是措辞与凭据检查，不证明片子好；回执本身由 reel deliver 的检查保证。
"""
import datetime, hashlib, json, os, re, shlex, sys

STATE = os.environ.get("REEL_STATE_DIR") or os.path.expanduser("~/.local/state/reel")
MAX_BLOCKS = 5
VIDEO_EXT = re.compile(r"\.(mp4|mov|m4v|webm|mkv)\b", re.I)
# 只认命令位置上的产出程序（片段开头），`which ffmpeg`、grep 里出现的词不算
PROD = re.compile(r"^(\S*/)?(ffmpeg|melt|remotion|hyperframes)\b|^(\S*/)?reel(\.py)?\s+(render|preview)\b"
                  r"|^(\S*/)?python3?\s+(-\S+\s+)*\S*(build|render|make|export|compose|cut|edit|video|reel|clip|montage)"
                  r"\w*\.py\b|^(\S*/)?python3?\s+\S*reel\.py\s+(render|preview)\b|^npx\s+(remotion|hyperframes)\b", re.I)
SEG = re.compile(r"\s*(?:;|&&|\|\||\||\n)\s*")
ENV_PREFIX = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
SCRATCH = re.compile(r"/tmp/|/private/tmp/|/var/folders/|scratchpad|/tests?/|reel_gate_|reel_test_")
# reel 引擎调用：reel / …/bin/reel / …/reel.py / python3 [-B …] …/reel.py（文件名正好是 reel.py；build_reel.py 是自写脚本）
_ENGINE = r"^(\S*/)?(python3?\s+(-\S+\s+)*(\S*/)?reel\.py|reel(\.py)?)"
REEL_ENGINE = re.compile(_ENGINE + r"(\s|$)")
REEL_DELIVERY = re.compile(_ENGINE + r"\s+(deliver|register)\b")
REEL_DELIVER = re.compile(_ENGINE + r"\s+deliver\b")
REEL_RENDER = re.compile(_ENGINE + r"\s+_?(render|preview)\b")     # reel 只有这两个子命令出片
# 本轮调用了 reel（决定默认作用范围）：片段开头是 reel / …/bin/reel / python3 …/reel.py，后面跟子命令
REEL_CALL = re.compile(r"^(\S*/)?(python3?\s+(-\S+\s+)*\S*reel\.py|reel)\s+[a-z]")
SCOPE_ENV = "REEL_GATE_SCOPE"
_DONE = r"(完成|搞定|做好|剪好|做完|剪完|出好|导出|交付|交给你)"
CLAIM = re.compile(rf"(已经?|都|全部|均)\s*{_DONE}|{_DONE}了|成片(在|已|放)|可以(看|发|用)了"
                   r"|(视频|成片|片子|剪辑|样片)\S{0,8}(完成|好了|搞定)"
                   r"|已(放|存|保存|复制|上传|导出)(到|进|在|好)|\bdone\b|\bdelivered\b|\bfinished\b", re.I)
DELIV = re.compile(r"成片|片子|样片|视频|剪辑|交付|导出|\bvideos?\b|\bfinal cut\b|\bdeliver(y|ed|able)?\b|\.mp4|\.mov|\bmp4\b|\bmov\b|\bv\d+(\.\d+)*\b", re.I)
HARD_DELIV = re.compile(r"成片|片子|样片|交付|导出|\bfinal cut\b|\bdeliver(y|ed|able)?\b|\.mp4|\.mov|\bv\d+(\.\d+)*\b", re.I)
DEV = re.compile(r"测试|代码|文档|检查器|脚本|hook|单测|用例|commit|提交|重构|修复|lint|README|PROGRESS"
                 r"|\btests?\b|\bcode\b|\bdocs?\b|\bscript\b|\brefactor|\bfix(ed)?\b", re.I)
HONEST = re.compile(r"待审样片|待审|未完成|没完成|尚未完成|还没(有)?完成|待试听|未试听|还没试听|未交付|没有交付|没交付"
                    r"|不能交付|无法交付|DELIVERY_NOT_FINAL|DELIVERY_BLOCKED"
                    r"|pending review|awaiting (review|a listen|listening)|not (yet )?(finished|done|complete|delivered|reviewed)"
                    r"|unreviewed|cannot be delivered|not been (listened to|reviewed|delivered)", re.I)
GLOBAL_HONEST = re.compile(r"本轮无视频交付|不涉及视频交付|没有视频交付|no video (was )?delivered (in )?this turn|no video delivery this turn", re.I)
SENT = re.compile(r"[^。！？!?\n]+")
LABEL = re.compile(r"\bv\d+(?:\.\d+)*\b", re.I)


def claim_sentences(msg):
    if not msg or GLOBAL_HONEST.search(msg):
        return []
    ss = [x.strip() for x in SENT.findall(msg) if x.strip()]
    out = []
    for i, x in enumerate(ss):
        if not CLAIM.search(x) or not DELIV.search(x):
            continue
        if DEV.search(x) and not HARD_DELIV.search(x):
            continue                        # 「测试都完成了」「代码改好了」不是交付声明
        if HONEST.search(x) or (i + 1 < len(ss) and HONEST.search(ss[i + 1])):
            continue
        out.append(x)
    return out


def _ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_human(e):
    if e.get("type") == "user" and e.get("isMeta"):
        return False
    m = e.get("message") or {}
    if m.get("role") != "user":
        return False
    c = m.get("content")
    if isinstance(c, str):
        return True
    return isinstance(c, list) and not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)


def load(path):
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass
    start = 0
    for i in range(len(entries) - 1, -1, -1):
        if _is_human(entries[i]):
            start = i
            break
    session_start = next((_ts(e.get("timestamp")) for e in entries if e.get("timestamp")), None)
    turn = [e for e in entries[start:] if not e.get("isSidechain")]
    return turn, session_start, (entries[start].get("uuid") if entries else None)


def _text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(_text(x.get("content") if x.get("type") == "tool_result" else x.get("text", ""))
                         for x in c if isinstance(x, dict))
    return ""


def analyse(turn):
    cmds, results, last = [], {}, ""
    for e in turn:
        m = e.get("message") or {}
        c = m.get("content")
        if not isinstance(c, list):
            continue
        if m.get("role") == "assistant":
            texts = []
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") == "Bash":
                    cmds.append((b.get("id"), (b.get("input") or {}).get("command") or "", _ts(e.get("timestamp"))))
                elif b.get("type") == "text":
                    texts.append(b.get("text", ""))
            if any(t.strip() for t in texts):
                last = "\n".join(texts)
        else:
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = _text(b.get("content"))
    return cmds, results, last


def _segments(cmd):
    """按引号感知地切成简单命令（引号里的换行/分号不切）；解析不了时退回按符号粗切。"""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars="();<>|&\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        out, cur = [], []
        for t in lex:
            if t in (";", "&&", "||", "|", "&", "\n", "(", ")", "{", "}", "()", "|&", ";;"):
                if cur:
                    out.append(" ".join(cur))
                cur = []
            else:
                cur.append(t)
        if cur:
            out.append(" ".join(cur))
        return out
    except ValueError:
        return SEG.split(cmd)


def _cmd_segments(full):
    """一个 Bash 块 → (去掉 heredoc 正文的命令, 简单命令片段)；片段去掉了开头的环境变量前缀。"""
    cmd = full.split("<<", 1)[0]
    return cmd, [ENV_PREFIX.sub("", x.strip().lstrip("({ ")) for x in _segments(cmd)]


def called_reel(cmds):
    return any(REEL_CALL.search(seg) for _, full, _ in cmds for seg in _cmd_segments(full)[1])


def _producing(seg):
    """片段是不是出片程序。reel 引擎只有 render / preview 出片；deliver、register、check、receipt 等子命令都不算。"""
    if REEL_ENGINE.search(seg):
        return bool(REEL_RENDER.search(seg))
    return bool(PROD.search(seg))


def video_work(cmds, results):
    """→ (理由, 本轮最后一次产出命令的时间, 最后一个「reel deliver 之后又出片」的块号或 None)。
    heredoc 正文（改文档/测试的 python - <<EOF）不算命令。块里有 deliver / register 也继续逐片段看产出；
    块内先后只看最后一个 deliver 片段（register 不算）。"""
    why, last, cut = None, None, None
    for k, (tid, full, ts) in enumerate(cmds):
        cmd, segs = _cmd_segments(full)
        dl = [i for i, x in enumerate(segs) if REEL_DELIVERY.search(x)]
        if dl:
            why = why or f"调用了 reel：{segs[dl[0]][:80]}"
        if SCRATCH.search(cmd):
            continue
        dv = [i for i in dl if REEL_DELIVER.search(segs[i])]
        prod = [i for i, seg in enumerate(segs) if _producing(seg)
                and (VIDEO_EXT.search(seg) or VIDEO_EXT.search(results.get(tid, "")))]
        if prod:
            why = f"视频产出命令：{segs[prod[0]][:80]}"
            last = ts or last
            if dv and prod[-1] > dv[-1]:
                cut = k                     # deliver 之后又出片：这一块及更早块里的回执不能背书
    return why, last, cut


def registered_project(cwd):
    try:
        with open(os.path.join(STATE, "registry.json"), encoding="utf-8") as fh:
            tasks = json.load(fh).get("tasks", [])
    except (OSError, ValueError, AttributeError):
        return None
    cwd = os.path.realpath(cwd or "")
    for t in tasks:
        p = os.path.realpath(t.get("project_dir") or "/nonexistent")
        if cwd == p or cwd.startswith(p + os.sep):
            return p
    return None


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def receipts(turn_text, after=None, project=None):
    """有效的回执：交付文件仍在且 hash 没变。project 给定 → 该项目的（不限时间）；否则 → 本轮输出里出现过、
    且晚于本轮最后一次产出的。返回 [(status, label, name)]。"""
    out = []
    try:
        lines = open(os.path.join(STATE, "deliveries.jsonl"), encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        brief = (r.get("paths") or {}).get("brief") or ""
        if project:
            if os.path.realpath(os.path.dirname(brief)) != project:
                continue
        else:
            if not (r.get("receipt") and r["receipt"] in turn_text) and not (r.get("delivered") and r["delivered"] in turn_text):
                continue
            ts = _ts(r.get("created_at"))
            if after and ts and ts + datetime.timedelta(seconds=1) < after:
                continue
        d = r.get("delivered")
        if not d or not os.path.isfile(d) or _sha(d) != r.get("delivered_sha256"):
            continue
        out.append((r.get("status"), r.get("label"), r.get("name")))
    return out


def _norm(name):
    return str(name or "").strip().lower()


def known_names():
    """已知作品名 = 登记表各任务的 name ∪ 交付流水各行的 name，只取至少 2 个字符的。→ {小写名: 原名}。"""
    raw = []
    try:
        with open(os.path.join(STATE, "registry.json"), encoding="utf-8") as fh:
            raw += [t.get("name") for t in json.load(fh).get("tasks", []) if isinstance(t, dict)]
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        with open(os.path.join(STATE, "deliveries.jsonl"), encoding="utf-8") as fh:
            for ln in fh:
                try:
                    raw.append(json.loads(ln).get("name"))
                except (ValueError, AttributeError):
                    pass
    except OSError:
        pass
    out = {}
    for n in raw:
        if isinstance(n, str) and len(n.strip()) >= 2:
            out.setdefault(_norm(n), n.strip())
    return out


def named_works(sentence, names):
    """句子里点名的已知作品（不分大小写的子串）；某次出现被更长的已知名的出现包住 → 这次不算。→ {小写名}。"""
    low, spans, hit = sentence.lower(), [], set()
    for n in sorted(names, key=len, reverse=True):
        occ, i = [], low.find(n)
        while i >= 0:
            occ.append((i, i + len(n)))
            i = low.find(n, i + 1)
        if any(not any(a <= s and e <= b and b - a > e - s for a, b in spans) for s, e in occ):
            hit.add(n)
        spans += occ
    return hit


def _bump(key):
    path = os.path.join(STATE, "stopgate.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d = {k: v for k, v in d.items() if k == key}          # 只留当前一轮
    d[key] = d.get(key, 0) + 1
    os.makedirs(STATE, exist_ok=True)
    json.dump(d, open(path, "w", encoding="utf-8"))
    if d[key] > MAX_BLOCKS:
        with open(os.path.join(STATE, "stopgate.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} released after {MAX_BLOCKS} blocks: {key}\n")
    return d[key]


def verdict(payload):
    path = payload.get("transcript_path")
    if not path:
        return None
    turn, _, turn_id = load(path)
    cmds, results, last = analyse(turn)
    proj = registered_project(payload.get("cwd"))
    if os.environ.get(SCOPE_ENV) != "global" and not proj and not called_reel(cmds):
        return None                         # 默认范围：既不在登记项目里、本轮也没调 reel → 不管
    last = payload.get("last_assistant_message") or last
    claims = claim_sentences(last)
    if not claims:
        return None
    why, last_prod, cut = video_work(cmds, results)
    if not why and not proj:
        return None
    if cut is None:
        turn_text = "\n".join(c for _, c, _ in cmds) + "\n" + "\n".join(results.values())
    else:                                   # 只认「deliver 之后又出片」那一块之后的块里拿到的回执
        later = cmds[cut + 1:]
        turn_text = "\n".join(c for _, c, _ in later) + "\n" + "\n".join(results.get(t, "") for t, _, _ in later)
    got = receipts(turn_text, after=last_prod) if why else receipts(turn_text, project=proj)
    finals = {lab for st, lab, _ in got if st == "final"}
    final_pairs = {(_norm(n), _norm(lab)) for st, lab, n in got if st == "final"}
    names = known_names()
    wanted, plain, miss, bad = set(), False, [], None
    for x in claims:
        labels = sorted({m.group(0).lower() for m in LABEL.finditer(x)})
        named = named_works(x, names)
        if not named:                       # 没点名作品：按版本号核对（旧规则）
            plain = True
            wanted.update(labels)
            continue
        lack = [names[n] for n in sorted(named) if not any(fn == n for fn, _ in final_pairs)]
        if len(lack) < len(named):          # 点名的作品都没有回执时，版本号缺什么不用再列
            lack += [f"{'/'.join(names[n] for n in sorted(named))} {lab}" for lab in labels
                     if not any(fn in named and fl == lab for fn, fl in final_pairs)]
        if lack and bad is None:
            bad = x
        miss += [m for m in lack if m not in miss]
    missing = sorted(wanted - {str(x).lower() for x in finals})
    if not miss and (not plain or (finals and not missing)):
        return None
    if _bump(f"{payload.get('session_id')}:{turn_id}") > MAX_BLOCKS:
        return None
    ctx = why or f"会话在登记过的 reel 项目里（{proj}）"
    if miss:
        have = sorted({f"{n} {lab}" for st, lab, n in got if st == "final"})
        note = (f"（有成片回执的是 {have}）" if have else "（只有待审样片回执，要说「待审样片」）" if got
                else "（回执要晚于本轮最后一次出片，且是本轮 reel deliver 写的）" if why else "")
        problem = f"声称的作品 {miss} 没有对应的成片回执{note}"
        claims = [bad]
    elif finals and missing:
        problem = f"声称的版本 {missing} 没有对应的成片回执（有回执的是 {sorted(finals)}）"
    elif any(st != "final" for st, _, _ in got):
        problem = "只有待审样片回执，要说「待审样片」"
    else:
        problem = "没有对应的成片回执" + ("（回执要晚于本轮最后一次出片，且是本轮 reel deliver 写的）" if why else "")
    return (f"[deliver-gate] 这句说了完成/交付：「{claims[0][:40]}」，但{problem}（{ctx}）。"
            "要么先走 `reel check` → `reel deliver` 拿到 status=final 的回执；要么在同一句里如实写明状态："
            "「待审样片」「未完成」「待试听」「未交付」（英文：pending review / not finished / not delivered）。"
            "只是讨论或开发、没有交付视频，就写明「本轮无视频交付」（英文：no video delivered this turn）。")


def main():
    try:
        payload = json.load(sys.stdin)
        reason = verdict(payload)
    except Exception:  # noqa: BLE001
        return
    if reason:
        json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
    sys.exit(0)
