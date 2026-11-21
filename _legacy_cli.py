"""engine_mac.py / engine_gpu.py 的共用薄壳：旧参数 → 旧文本 plan 适配 → reelcore 渲染 + 验收。"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reelcore import plan as P, render  # noqa: E402
from reelcore.errors import ReelError  # noqa: E402


def main(backend, doc):
    ap = argparse.ArgumentParser(description=doc)
    ap.add_argument("--plan", required=True, help="旧文本 plan：文件名|入点|时长|运镜(in/out/panL/panR/still/drift)")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--subs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", required=True, help="素材目录（不再有项目专属默认值）")
    ap.add_argument("--xfade", type=float, default=0.0, help=">0 启用转场(秒)；画面会自动补齐到配音长度")
    ap.add_argument("--bgm", default="")
    ap.add_argument("--grade", action="store_true", help="电影感调色（与 --bgm 可同时用）")
    ap.add_argument("--lut", default="", help="胶片 LUT .cube")
    ap.add_argument("--size", default="1080x1920x60", help="WxH 或 WxHxFPS")
    ap.add_argument("--keywords", default="", help="逗号分隔的金黄高亮关键词")
    ap.add_argument("--keep-tmp", action="store_true")
    a = ap.parse_args()
    w, h, *f = [int(x) for x in a.size.split("x")]
    try:
        plan = P.from_legacy(a.plan, os.path.abspath(a.src), os.path.abspath(a.voice), os.path.abspath(a.subs),
                             w, h, f[0] if f else 60, a.xfade, os.path.abspath(a.bgm) if a.bgm else None, a.grade,
                             os.path.abspath(a.lut) if a.lut else None,
                             [k for k in a.keywords.split(",") if k])
        r = render.render(plan, a.out, backend, keep_tmp=a.keep_tmp)
    except ReelError as e:
        print(json.dumps({"ok": False, "error": e.to_dict()}, ensure_ascii=False, indent=1), file=sys.stderr)
        return 1
    bad = [c for c in r["checks"] if not c["ok"]]
    if bad:
        print("[FAIL] 成片验收未通过:\n" + json.dumps(bad, ensure_ascii=False, indent=1), file=sys.stderr)
        return 1
    for adj in r["adjustments"]:
        print(f"[fit] {adj['shot']} {adj['frames_from']}→{adj['frames_to']} 帧（补齐到配音长度）")
    print(f"[done] {a.out}  验收 {len(r['checks'])} 项通过  {r['elapsed_s']}s")
    return 0
