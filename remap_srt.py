#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""时间映射：按 keeps(保留段) 把词级 words.srt 的时间戳平移到"切后时间轴"，
丢弃落在删除区的词，再按标点/字数重新分句成可读字幕 → voice_cut.srt。

这是"不二次转写"的关键：真相源(words.srt)只转写一次，切后字幕由数学平移得出，
与净音频逐帧对齐、零新增识别错误、保持词级精度。

映射：keeps=[(a0,b0),(a1,b1),...](原始时间, 已排序)。原始时刻 t 落在第 k 段时,
新时刻 = (前 k 段总长) + (t - a_k)；落在删除区的词按其"中心"判定并丢弃。

用法: remap_srt.py words.srt keeps.json --out voice_cut.srt
        [--max-chars 16] [--gap-break 0.6] [--min-cue 0.4]
"""
import sys, json, argparse
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from detect_fluffs import parse   # 复用词级 SRT 解析

BREAK_PUNCT = "。！？!?…；;"          # 句末 → 强制断句
SOFT_PUNCT = "，、,"                   # 句中逗号 → 够长才断


def fmt(t):
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:                    # 进位修正
        s += 1; ms = 0
        if s == 60: s = 0; m += 1
        if m == 60: m = 0; h += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_mapper(keeps):
    """返回 (map_time, locate)。map_time(t)->新时刻; locate(t)->所在保留段索引或 None。"""
    segs = []; off = 0.0
    for a, b in keeps:
        segs.append((a, b, off)); off += (b - a)
    total = off

    def locate(t):
        for i, (a, b, _) in enumerate(segs):
            if a - 1e-6 <= t <= b + 1e-6:
                return i
        return None

    def map_time(t):
        # 落在某保留段内 → 直接平移
        for a, b, o in segs:
            if a - 1e-6 <= t <= b + 1e-6:
                return o + (t - a)
        # 落在删除区 → 贴到下一段开头(其新时刻=该段offset); 超过末尾→total
        for a, b, o in segs:
            if t < a:
                return o
        return total

    return map_time, locate, segs, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("words"); ap.add_argument("keeps")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-chars", type=int, default=16)
    ap.add_argument("--gap-break", type=float, default=0.6)
    ap.add_argument("--min-cue", type=float, default=0.4)
    a = ap.parse_args()

    words = parse(a.words)
    data = json.load(open(a.keeps))
    keeps = [(float(s), float(e)) for s, e in data["keeps"]]
    map_time, locate, segs, total = build_mapper(keeps)

    # 1) 平移每个词；中心落在删除区的丢弃；保留词钳到所在段内
    kept = []
    for ws, we, txt in words:
        if not txt.strip():
            continue
        c = (ws + we) / 2.0
        si = locate(c)
        if si is None:
            continue                  # 整词落在删除区
        a0, b0, o0 = segs[si]
        ns = o0 + (max(ws, a0) - a0)
        ne = o0 + (min(we, b0) - a0)
        if ne <= ns:
            ne = ns + 0.04            # 退化保护：至少 40ms
        kept.append((ns, ne, txt.strip()))

    # 2) 重新分句成可读字幕(标点 / 字数 / 自然停顿 三选一断句)
    cues = []
    buf = []; cs = None; ce = None
    for idx, (ns, ne, txt) in enumerate(kept):
        if cs is None:
            cs = ns
        buf.append(txt); ce = ne
        text = "".join(buf)
        nlen = len(text.replace(" ", ""))
        last = txt[-1] if txt else ""
        nxt_gap = (kept[idx + 1][0] - ne) if idx + 1 < len(kept) else 1e9
        brk = (last in BREAK_PUNCT
               or nlen >= a.max_chars
               or (last in SOFT_PUNCT and nlen >= a.max_chars * 0.6)
               or nxt_gap >= a.gap_break)
        if brk:
            cues.append((cs, max(ce, cs + a.min_cue), text))
            buf = []; cs = None; ce = None
    if buf:
        cues.append((cs, max(ce, cs + a.min_cue), "".join(buf)))

    # 3) 防重叠：起点永远用词级真实时间，不后推；只把结束时间收到下一句起点/音频末尾之前
    fixed = []
    for i, (s, e, t) in enumerate(cues):
        nxt = cues[i + 1][0] if i + 1 < len(cues) else total
        e2 = min(e, nxt, total)
        if e2 <= s:                       # 下一句紧贴着开始：保留自然结束，但不越过下一句
            e2 = min(max(s + 0.01, min(e, nxt)), total)
        fixed.append((s, e2, t))
    cues = fixed

    with open(a.out, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(cues, 1):
            f.write(f"{i}\n{fmt(s)} --> {fmt(e)}\n{t}\n\n")

    print(f"映射 {len(words)} 词 → 保留 {len(kept)} 词 → {len(cues)} 句字幕")
    print(f"净时长 {total:.2f}s, 末句 {cues[-1][1] if cues else 0:.2f}s -> {a.out}")


if __name__ == "__main__":
    main()
