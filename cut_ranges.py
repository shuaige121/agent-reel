#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从音频剪掉指定时间区间(废段/气口), 保留其余精确拼接(atrim+concat, 采样级)。
同时输出"保留段"keeps.json —— 它就是时间映射的全部信息(remap_srt.py 据此把
词级时间戳平移到切后时间轴, 无需二次转写)。

用法: cut_ranges.py <in_audio> <out_audio> "s1-e1,s2-e2,..." [--keeps keeps.json]
                     [--ar 48000] [--ac 1]
  位置参数与旧版兼容; 空区间("")表示不剪, 输出=整段, keeps=[[0,dur]]。"""
import sys, json, argparse, subprocess


def audio_duration(path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path]).decode().strip())


def keeps_from_cuts(cuts, dur, eps=0.02):
    """删除区间 → 保留区间(原始时间轴, 已排序合并)。"""
    cuts = sorted((min(max(float(a), 0.0), dur), min(max(float(b), 0.0), dur)) for a, b in cuts)
    for a, b in cuts:
        if b < a:
            raise ValueError(f"删除区间起点晚于终点: {a}-{b}")
    keeps = []; pos = 0.0
    for a, b in cuts:
        if a > pos + eps:
            keeps.append((pos, a))
        pos = max(pos, b)
    if pos < dur - eps:
        keeps.append((pos, dur))
    return keeps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out"); ap.add_argument("spec")
    ap.add_argument("--keeps", default=None)
    ap.add_argument("--ar", type=int, default=None)
    ap.add_argument("--ac", type=int, default=None)
    a = ap.parse_args()

    cuts = [r.split("-") for r in a.spec.split(",") if r.strip()]
    dur = audio_duration(a.inp)
    try:
        keeps = keeps_from_cuts(cuts, dur)
    except ValueError as e:
        print(f"ERROR: {e}"); sys.exit(1)
    if not keeps:
        print("ERROR: 全被剪没了"); sys.exit(1)

    parts, labels = [], []
    for k, (s, e) in enumerate(keeps):
        parts.append(f"[0]atrim={s:.3f}:{e:.3f},asetpts=N/SR/TB[s{k}]")
        labels.append(f"[s{k}]")
    fc = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(keeps)}:v=0:a=1[o]"
    cmd = ["ffmpeg", "-y", "-i", a.inp, "-filter_complex", fc, "-map", "[o]"]
    if a.ar:
        cmd += ["-ar", str(a.ar)]
    if a.ac:
        cmd += ["-ac", str(a.ac)]
    cmd += [a.out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-400:]); sys.exit(1)

    out_dur = sum(e - s for s, e in keeps)
    if a.keeps:
        json.dump({"keeps": [[round(s, 3), round(e, 3)] for s, e in keeps],
                   "src_dur": round(dur, 3), "out_dur": round(out_dur, 3)},
                  open(a.keeps, "w"))
    print(f"剪掉 {len(cuts)} 段, 保留 {len(keeps)} 段, "
          f"{dur:.2f}s→{out_dur:.2f}s -> {a.out}")


if __name__ == "__main__":
    main()
