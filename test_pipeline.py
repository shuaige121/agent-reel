#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性单测: 证明 keeps 推导 + 时间映射逐帧精确, 覆盖边界。无外部依赖, 不调 ffmpeg/whisper。
运行: python3 test_pipeline.py"""
import os, sys, json, tempfile, subprocess
D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
from cut_ranges import keeps_from_cuts
from remap_srt import build_mapper
import build_cut_plan as bcp

ok = 0


def eq(a, b, msg, tol=1e-6):
    global ok
    cond = abs(a - b) <= tol if isinstance(a, (int, float)) else a == b
    assert cond, f"FAIL {msg}: {a} != {b}"
    ok += 1


# ---- keeps_from_cuts ----
eq(keeps_from_cuts([(1, 2)], 4), [(0.0, 1.0), (2.0, 4.0)], "中段删")
eq(keeps_from_cuts([], 4), [(0.0, 4.0)], "空计划=整段")
eq(keeps_from_cuts([(0, 1)], 4), [(1.0, 4.0)], "开头静音裁掉")
eq(keeps_from_cuts([(3, 4)], 4), [(0.0, 3.0)], "结尾裁掉")
eq(keeps_from_cuts([(1, 2), (1.5, 2.5)], 4), [(0.0, 1.0), (2.5, 4.0)], "重叠切合并")
eq(keeps_from_cuts([(1, 2), (3, 3.5)], 5), [(0.0, 1.0), (2.0, 3.0), (3.5, 5.0)], "两段删")

# ---- build_mapper: 逐帧映射 ----
mp, loc, segs, total = build_mapper([(0, 1), (2, 4)])
eq(total, 3.0, "净时长=保留段总和")
eq(mp(0), 0.0, "起点"); eq(mp(1), 1.0, "段尾"); eq(mp(0.5), 0.5, "段内")
eq(mp(1.5), 1.0, "删除区→贴下段开头")      # 关键: 落在删除区的时刻贴到下一保留段
eq(mp(2), 1.0, "下段开头"); eq(mp(3), 2.0, "下段中"); eq(mp(4), 3.0, "末尾")
eq(mp(5), 3.0, "越界→total")
eq(loc(0.5), 0, "locate seg0"); eq(loc(3), 1, "locate seg1")
assert loc(1.5) is None, "FAIL: 删除区 locate 应为 None"; ok += 1

# ---- build_cut_plan 纯函数: 静音∩词间隙 + 区间运算 ----
# 词: [0,0.4][0.5,0.9](间隙0.4-0.5=0.1) ... 间隙[0.9,2.0]=1.1s 是大停顿
words = [(0.0, 0.4, "你"), (0.5, 0.9, "好"), (2.0, 2.4, "再"), (2.4, 2.8, "见")]
gaps = bcp.word_gaps(words, 3.0)
assert (0.9, 2.0) in gaps, f"FAIL 词间隙: {gaps}"; ok += 1
# 静音[1.0,1.9] 落在大间隙内, keep_pad=0.12 → 删[0.9+0.12,1.9]=[1.02,1.9]? 交集是[1.0,1.9]
ov = bcp.overlap(1.0, 1.9, 0.9, 2.0)
eq(ov, (1.0, 1.9), "静音∩间隙取交集")
eq(bcp.overlap(0.0, 0.3, 0.5, 0.9), None, "无交集→None")
# coalesce + subtract
eq(bcp.coalesce([(1, 2), (1.5, 3)]), [(1.0, 3.0)], "coalesce 合并")
eq(bcp.subtract([(0, 5)], [(2, 3)]), [(0.0, 2.0), (3.0, 5.0)], "subtract 挖洞")
eq(bcp.subtract([(0, 5)], []), [(0, 5)], "subtract 空保护")

# ---- 端到端 remap (子进程, 真跑脚本) ----
with tempfile.TemporaryDirectory() as t:
    ws = os.path.join(t, "words.srt"); kp = os.path.join(t, "keeps.json"); out = os.path.join(t, "cut.srt")
    # 6 词, 每 0.4s; 删除纯间隙 [1.2,2.0]
    srt = ""
    spans = [(0.0, 0.4, "你"), (0.4, 0.8, "好"), (0.8, 1.2, "呀"),
             (2.0, 2.4, "世"), (2.4, 2.8, "界"), (2.8, 3.2, "。")]

    def f(x):
        h = int(x // 3600); m = int(x % 3600 // 60); s = int(x % 60); ms = int(round((x - int(x)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    for i, (a, b, tx) in enumerate(spans, 1):
        srt += f"{i}\n{f(a)} --> {f(b)}\n{tx}\n\n"
    open(ws, "w").write(srt)
    keeps = keeps_from_cuts([(1.2, 2.0)], 3.2)
    json.dump({"keeps": [[s, e] for s, e in keeps], "src_dur": 3.2,
               "out_dur": sum(e - s for s, e in keeps)}, open(kp, "w"))
    r = subprocess.run([sys.executable, os.path.join(D, "remap_srt.py"), ws, kp, "--out", out],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"FAIL remap 退出码: {r.stderr}"; ok += 1
    body = open(out).read()
    # 世界。三词被左移 0.8s: 2.0→1.2, 3.2→2.4; 末句 end 应=净时长2.4
    assert "你好呀世界。" in body, f"FAIL 文本合并: {body!r}"; ok += 1
    assert "00:00:02,400" in body, f"FAIL 末句应到 2.4s: {body!r}"; ok += 1
    # 净时长 = 3.2 - 0.8 = 2.4
    eq(sum(e - s for s, e in keeps), 2.4, "净时长 2.4s")

print(f"✅ 全部通过 ({ok} 断言)")
