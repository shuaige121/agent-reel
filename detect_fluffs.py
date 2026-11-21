#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从逐词 SRT(whisper --max-len 1) 检测"相邻重复/重起"候选废段(口误重录)。
输出候选剪除区间 start-end + 文本, 供人确认后交给 cut_ranges.py。
用法: detect_fluffs.py words.srt
注意: 只是候选, 枚举类(海洋球池/彩虹滑梯…)或强调重复需人工排除。"""
import sys, re

def parse(path):
    out=[]
    for b in re.split(r"\n\s*\n", open(path, encoding="utf-8").read().strip()):
        ls=b.split("\n")
        ti=next((i for i,l in enumerate(ls) if "-->" in l), None)
        if ti is None: continue
        m=re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", ls[ti])
        if not m: continue
        g=list(map(int,m.groups()))
        st=g[0]*3600+g[1]*60+g[2]+g[3]/1000.0
        en=g[4]*3600+g[5]*60+g[6]+g[7]/1000.0
        txt="".join(ls[ti+1:]).strip()
        out.append((st,en,txt))
    return out

def find_repeats(w):
    """检测"相邻重复/重起"候选废段(口误重录)。返回 [(start,end,text), ...]。
    供 detect_fluffs 自身与 build_cut_plan 共用, 算法保持一致。"""
    n=len(w); cuts=[]; i=0
    while i<n:
        hit=None
        for L in range(16,1,-1):                # 重起短语长度, 长优先(抓整句重复)
            if i+L>n: continue
            phrase=tuple(w[k][2] for k in range(i,i+L))
            if len("".join(phrase))<4: continue # 滤掉<4字的(多为有意重复/巧合)
            for gap in range(0,6):              # 假开始与重起之间夹的词数
                q=i+L+gap
                if q+L<=n and tuple(w[k][2] for k in range(q,q+L))==phrase:
                    cuts.append((w[i][0], w[q][0], "".join(w[k][2] for k in range(i,q))))
                    i=q; hit=True; break
            if hit: break
        if not hit: i+=1
    return cuts

def main():
    w=parse(sys.argv[1])
    cuts=find_repeats(w)
    if not cuts:
        print("未检出明显重复废段。"); return
    print("候选剪除区间(确认后用 cut_ranges.py):")
    print(",".join(f"{a:.2f}-{b:.2f}" for a,b,_ in cuts))
    for a,b,t in cuts:
        print(f"  {a:6.2f}-{b:6.2f}  ({b-a:.2f}s)  「{t}」")

if __name__=="__main__":
    main()
