#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一切割计划：从词级 words.srt + 音频静音检测，算出要删除的区间(原始时间轴)。

删除区间来自三类(全部以"原始音频时间"表示)；1、2 类只是候选，需 --accept 采纳才删：
  1) 重复重起(口误重录) —— 复用 detect_fluffs.find_repeats，整段删
  2) 填充词(嗯/呃/那个/就是…) —— 词表匹配，删该词时间span
  3) 气口 —— silencedetect 静音边沿 ∩ 词与词之间的空隙；交集 > --min-gap 才动，
            且只删"超出 --keep-pad 的部分"(留一点自然停顿，不会把字头字尾削掉)

设计要点：只删"既是静音、又落在词间"的区段——whisper 词级 end-time 偏松，常拖进
后面的静音里，单看 SRT 词间隙会切歪；和真实静音取交集才干净。

输出：把删除区间写成 "s-e,s-e,..."(秒) 到 --out 文件，供 cut_ranges.py 使用；
      分类详单打到 stdout 供人核对(重复/填充词可能误报，气口收缩是安全的)。

用法:
  build_cut_plan.py words.srt audio.wav --out cut_plan.txt
      [--keep-pad 0.12] [--min-gap 0.30] [--noise -32dB] [--sil-dur 0.18]
      [--fillers 嗯,呃,那个,就是,然后那个] [--no-fillers] [--no-repeats]
      [--exclude s-e,s-e]   # 保护区间：与之重叠的删除会被裁掉，避免误删
"""
import sys, os, re, json, argparse, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detect_fluffs import parse, find_repeats   # 复用同一份解析与重复检测

DEFAULT_FILLERS = ["嗯", "呃", "啊", "额", "那个", "这个", "就是", "然后那个", "那个那个"]


def audio_duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path]).decode().strip()
    return float(out)


def detect_silences(path, noise, sil_dur):
    """ffmpeg silencedetect → [(start,end), ...]（检测，不删除）。"""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-af", f"silencedetect=noise={noise}:d={sil_dur}", "-f", "null", "-"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"❌ silencedetect 失败（不能当作「没有静音」继续）:\n{r.stderr[-600:]}")
    log = r.stderr
    sils = []
    cur = None
    for m in re.finditer(r"silence_(start|end):\s*(-?[\d.]+)", log):
        kind, val = m.group(1), float(m.group(2))
        if kind == "start":
            cur = val
        elif kind == "end" and cur is not None:
            sils.append((max(0.0, cur), val))
            cur = None
    return sils


def word_gaps(words, dur):
    """词与词之间(含开头静音、结尾静音)的空隙区间 [(gs,ge), ...]。"""
    gaps = []
    if words and words[0][0] > 0:
        gaps.append((0.0, words[0][0]))               # 开头到第一个词
    for i in range(len(words) - 1):
        ge = words[i + 1][0]                            # 下一个词的开始
        gs = words[i][1]                               # 当前词的结束
        if ge > gs:
            gaps.append((gs, ge))
    if words and dur > words[-1][1]:
        gaps.append((words[-1][1], dur))               # 最后一个词到结尾
    return gaps


def overlap(a0, a1, b0, b1):
    """两区间交集 (s,e)；无交集返回 None。"""
    s, e = max(a0, b0), min(a1, b1)
    return (s, e) if e > s else None


def coalesce(intervals, eps=0.01):
    """排序+合并相邻/重叠区间。"""
    iv = sorted((a, b) for a, b in intervals if b - a > eps)
    if not iv:
        return []
    out = [list(iv[0])]
    for a, b in iv[1:]:
        if a <= out[-1][1] + eps:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def subtract(cuts, protects):
    """从 cuts 里减去 protects(保护区)，返回裁剪后的删除区间。"""
    if not protects:
        return cuts
    out = []
    for a, b in cuts:
        segs = [(a, b)]
        for pa, pb in protects:
            nxt = []
            for s, e in segs:
                ov = overlap(s, e, pa, pb)
                if not ov:
                    nxt.append((s, e)); continue
                if s < ov[0]:
                    nxt.append((s, ov[0]))
                if ov[1] < e:
                    nxt.append((ov[1], e))
            segs = nxt
        out.extend(segs)
    return out


def parse_ranges(spec):
    out = []
    for r in (spec or "").split(","):
        r = r.strip()
        if not r:
            continue
        a, b = r.split("-")
        out.append((float(a), float(b)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("words"); ap.add_argument("audio")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-pad", type=float, default=0.12)
    ap.add_argument("--min-gap", type=float, default=0.30)
    ap.add_argument("--noise", default="-32dB")
    ap.add_argument("--sil-dur", type=float, default=0.18)
    ap.add_argument("--fillers", default=",".join(DEFAULT_FILLERS))
    ap.add_argument("--no-fillers", action="store_true")
    ap.add_argument("--no-repeats", action="store_true")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--accept", default="", help="采纳的候选 id（如 r1,f3）；重复/填充词默认只列候选不删")
    ap.add_argument("--auto-fluffs", action="store_true", help="全部候选直接删除（旧行为，易误删）")
    a = ap.parse_args()

    words = parse(a.words)
    if not words:
        sys.stderr.write("⚠ words.srt 为空，无可切\n")
        open(a.out, "w").write("")
        return
    dur = audio_duration(a.audio)

    repeats, fillers, breaths = [], [], []

    # 1) 重复重起
    if not a.no_repeats:
        repeats = find_repeats(words)

    # 2) 填充词
    if not a.no_fillers:
        fset = {x.strip() for x in a.fillers.split(",") if x.strip()}
        for ws, we, txt in words:
            if txt.strip().strip("，。、,.") in fset:
                fillers.append((ws, we, txt))

    # 3) 气口：静音 ∩ 词间隙，删超出 keep-pad 的部分
    sils = detect_silences(a.audio, a.noise, a.sil_dur)
    gaps = word_gaps(words, dur)
    for ss, se in sils:
        for gs, ge in gaps:
            ov = overlap(ss, se, gs, ge)
            if not ov:
                continue
            ov_len = ov[1] - ov[0]
            if ov_len < a.min_gap:
                continue
            cut_start = ov[0] + a.keep_pad          # 紧挨前一个词留一点停顿
            cut_end = ov[1]
            if cut_end - cut_start > 0.01:
                breaths.append((cut_start, cut_end))

    # 重复/填充词只是候选：检测不懂语义（有意重复、「就是」作实词都会命中），删不删由决策层决定
    cands = [{"id": f"r{i}", "kind": "repeat", "start": round(s, 3), "end": round(e, 3), "text": t}
             for i, (s, e, t) in enumerate(repeats, 1)]
    cands += [{"id": f"f{i}", "kind": "filler", "start": round(s, 3), "end": round(e, 3), "text": t.strip()}
              for i, (s, e, t) in enumerate(fillers, 1)]
    accept = {x.strip() for x in a.accept.split(",") if x.strip()}
    unknown = accept - {c["id"] for c in cands}
    if unknown:
        raise SystemExit(f"❌ --accept 里有不存在的候选: {sorted(unknown)}")
    chosen = [c for c in cands if a.auto_fluffs or c["id"] in accept]
    with open(a.out + ".candidates.json", "w", encoding="utf-8") as fh:
        json.dump({"candidates": cands, "accepted": [c["id"] for c in chosen]}, fh, ensure_ascii=False, indent=1)

    # 汇总 → 合并 → 减去保护区
    raw = [(c["start"], c["end"]) for c in chosen] + breaths
    protects = coalesce(parse_ranges(a.exclude))
    cuts = coalesce(subtract(coalesce(raw), protects))

    open(a.out, "w").write(",".join(f"{s:.3f}-{e:.3f}" for s, e in cuts))

    # 人工核对详单（重复/填充词可能误报；气口收缩安全）
    removed = sum(e - s for s, e in cuts)
    print(f"原音频 {dur:.2f}s → 预计删除 {removed:.2f}s → 净约 {dur - removed:.2f}s")
    print(f"删除区间 {len(cuts)} 段，写入 {a.out}")
    if cands:
        print(f"【候选 {len(cands)} 条，已采纳 {len(chosen)} 条】详单 {a.out}.candidates.json；"
              "采纳用 --accept r1,f2（或 REEL_ACCEPT=r1,f2 重跑 reel-prep）")
        for c in cands:
            mark = "✓删" if c in chosen else "  留"
            print(f"  {mark} {c['id']:>4} {c['kind']:6} {c['start']:7.2f}-{c['end']:7.2f} 「{c['text']}」")
    print(f"【气口收缩·安全】{len(breaths)} 段静音被压到留白 {a.keep_pad}s")


if __name__ == "__main__":
    main()
