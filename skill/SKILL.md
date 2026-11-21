---
name: reel
description: >-
  做视频/剪视频/出片的入口：宣传片、口播、MV/歌词视频、照片+配乐+配音、素材剪成片、推镜头特写、网页边讲边高亮。交付必须走 reel check → reel deliver，拿到成片回执才能说完成。Use for making or editing a video, reel, promo, lyric video or MV; delivery must pass reel check → reel deliver and produce a final receipt.
---

# reel — agent 的视频制作 / 编辑工具

ffmpeg 为核心的分层工具：agent 看素材、定目标、写 plan；代码负责取景几何、渲染和验收。比例可配（默认竖屏 1080×1920）。
命令和格式细节在引擎 `README` 与 `docs/engine.zh.md`，动手前先读。

## 交付闸门（必须走）

规则没人执行就等于没有，下面每一步都有代码拦着：不走就交不出「成片」。

1. **开工先写 brief**（`$P/brief.json`）并登记：`reel register brief.json`。写清交付语言、成片还是样片、交付目录、不许改什么；写不出就问用户。登记后，Bash 里用 cp/mv/ffmpeg 直接写进交付目录，或用 `reel … --out PATH` / `--out=PATH`（`--png`、`--sheet`、`--report`、`--check-png` 同理）指向交付目录，会被 `hooks/guard_delivery.py` 拒绝。这个 guard 只解析命令文本，尽力而为：通配符、花括号、命令替换、脚本内部和远程写入可能拦不住，别拿它当许可，交付只走 `reel deliver`。
2. **声音先试听**：没有固定默认声音。先出 10–20 秒样本给人听，同意后把原话、样本路径、样本 sha256 写进 `brief.voice.approved`；换声音要重新试听。
3. **出片后检查**：`reel check 成片.mp4 --plan plan.json --brief brief.json [--profile default|social-vertical|项目.json]`。exit 1 = 不能交。看 `result.gate.blockers`（技术不合格）和 `pending_review`（要人确认）；`not_covered` 列的是没检查的项，不能说「已检查」。
4. **审片**：`needs_review` 逐项看（`reel scrub` / 抽帧），每项写一条带时间码的审片记录；完整看一遍写 `final_watch`，听片写 `final_listen`。
5. **交付**：`reel deliver 成片.mp4 --brief brief.json --version v3 --plan plan.json --reviews reviews.json`。不满足成片条件报 `DELIVERY_NOT_FINAL`，只能 `--sample`，文件名带「待审样片」。同版本号不能再交，旧版不删不覆盖。
6. **说「完成」的条件**：拿到 `status: final` 的回执（`reel receipt <回执>` 可复核）。否则如实说「未完成 / 待审样片 / 待试听」。`hooks/deliver_gate.py`（Stop hook）会拦没有回执的「已完成」；回执要对应你说的那件作品（点名作品和版本号时按作品名、版本号匹配）。该 hook 默认只管登记过的项目和调用过 reel 的回合，`REEL_GATE_SCOPE=global` 才是全机范围。
7. 项目自写的渲染脚本出的片也一样走 3–6。
8. MV：加 `--contract mv.json`。歌词检查读的是契约声明的歌词字层（每句演唱期间字层上有字、开唱前没有字），不读成片画面的像素，所以不能据此说「画面上的歌词已核对」。`song_level` 对合集和 `policy: "trim"` 逐首比较响度与真峰值，容差与单曲相同。
9. 参数错误也输出 JSON（`ARG_INVALID`，退出码 2），`--help` 输出帮助文本。`reel voice check` 核对中文（默认 `--lang zh`）另需 `pypinyin`（装进同一个 venv），缺了报 `BACKEND_UNAVAILABLE`；`reel words` 只需 `whisper-cli` 和模型。

brief.json 的例子（MV 另加 `"kind": "mv"`）：

```json
{"version": "reel.brief.v1", "name": "招生宣传", "language": "zh-CN", "deliverable": "final",
 "delivery_root": "~/Deliveries/<项目>", "must_not_change": ["片尾署名"],
 "voice": {"mode": "tts", "voice_id": "zh-CN-XiaoxiaoNeural",
           "approved": {"voice_id": "zh-CN-XiaoxiaoNeural", "by": "user", "quote": "这个声音可以",
                        "sample": "voice_sample.mp3", "sample_sha256": "<shasum -a 256 的结果>"}},
 "profile": "default"}
```

- brief 里只有 `name`、`must_not_change` 和键名为 `text` / `title` / `caption` / `label` 的字段会进文本扫描；`language`、`delivery_root`、`voice.*` 不扫。
- 没有配音写 `"voice": {"mode": "none"}`；真人录音写 `"recorded"`。静音版用项目 profile：`{"extends": "default", "audio": "silent"}`。
- profile：`default`（通用阈值）、`social-vertical`（竖屏社交平台：1080×1920 30fps、-16 LUFS）；项目差异写项目 profile（`extends`），有意的长停留登记进 `allow` 并写理由。文本扫描的词表在 profile 的 `text` 段：`banned` 和 `extra_banned`（不分大小写子串，命中 fail，项目自己的禁用词加在 `extra_banned`）、`banned_words`（区分大小写的大写整词 `TODO` / `TBD` / `FIXME`，命中 fail）、`review`（`placeholder`、`lorem ipsum`，整词，命中 needs_review，要写 `text_scan` 审片记录）。

## 每个 Bash 调用块的开头

agent 的每次 Bash 调用是全新 shell，函数不会保留，所以 `reel` 是 `PATH` 上的脚本（`bin/reel`），不是函数。它按 `REEL_PYTHON` → `~/.local/share/reel/venv/bin/python` → `python3` 的顺序选解释器。环境有问题先跑 `reel doctor`（列出必需和可选依赖及修复办法，必需项缺失退出 1）：

```bash
command -v reel >/dev/null || export PATH="$HOME/.claude/plugins/reel/bin:$PATH"
P="$HOME/work/<project>"; mkdir -p "$P/preview" && cd "$P"   # 产物落项目目录，不落 /tmp
```

## 标准工作流

1. **备素材**到项目目录 `$P`（素材、observations.json、plan.json、预览、成片都放这里；一个目录一个 plan，`.reel-history/` 记修订）。
2. **看素材、定目标**：
   ```bash
   reel inspect 素材/a.jpg 素材/b.mp4
   reel frame 素材/a.jpg --out "$P/preview/a_grid.png"          # 读带 0–1 网格的图，读出目标框
   reel observe --obs observations.json --src 素材 --asset a.jpg --bbox x0,y0,x1,y1 --label "目标"
   reel locate  --obs observations.json --src 素材 --asset b.mp4 --t 2 --query "red cup" --check-png "$P/preview/chk.png"  # OWLv2，需要 torch
   ```
3. **写 plan.json**（EditPlan v1）：`camera: []` 真静止；`zoom_to` 引用 observation 推到目标；`track` 跟随多个时间点的 observation；`transition_out` 转场。视频镜头不要跨 `inspect` 给出的切镜点。
4. **校验 + 预览**：`reel validate plan.json`；`reel compile plan.json`；`reel preview plan.json s2 --out "$P/preview/s2.mp4" --sheet "$P/preview/s2.png"`，看拼图确认运镜。
5. **出片**：`reel render plan.json --out "$P/成片.mp4" --sheet "$P/preview/final.png" --where mac`。成片音频固定为 AAC 48 kHz，`checks` 含采样率。`checks` 全过只说明渲染没坏，接着走交付闸门 3–6。

## 时间线与卡点

- 看：`reel timeline plan.json --format text`；`reel timeline plan.json --png tl.png [--beats beats.json]`；`reel scrub plan.json --cuts --render 成片.mp4 --out 目录`。
- 改：`reel edit plan.json --op '{"op":"trim","id":"s3","duration":2.4}'`（每步校验后写入）；`reel undo plan.json --to r2`；`reel history plan.json`。
- 卡点：`reel beats 音乐.mp3 --out beats.json` → `reel snap plan.json --beats beats.json --mode downbeat`（`--dry-run` 先看报告）。

## 网页：边讲边跟随边高亮

素材是网页时不做 OCR：文字位置直接读 DOM，镜头和高亮由浏览器逐帧绘制。

1. 按网页原文写口播（要高亮的句子与页面一字不差），TTS 生成 `voice.mp3`。
2. `reel words voice.mp3 --out words.json`（本机 whisper 逐字时间）。
3. plan 里用 `kind: "web"` 镜头、`camera: [{"op": "follow"}]`、`highlight: [{"text": "页面原文…", "sync": "voice"}]`，`audio.words` 指向 `words.json`。
4. 报 `TEXT_NOT_FOUND` = 页面没有这段原文；`TEXT_NOT_IN_VOICE` = 配音对不上。

## 远程 GPU（可选）

- 设 `REEL_REMOTE=user@gpu-host`（可空格分隔多个）后，`reel render --where 5090` / `--where auto` 会把渲染派到远程 NVIDIA 主机（NVENC + libass），产物拉回本机再验收一次。不设则只用本机。
- 显存默认 ≤80%，超过报 `NEED_USER_APPROVAL`，先告诉用户，同意后才加 `--allow-over-80`；GPU 利用率 ≥50% 报 `GPU_BUSY`，不抢。远程 flock 保证同一时间只一个 reel GPU 任务。
- `reel remote probe` 查状态；`reel remote setup-vision` 在远程建独立 venv（torch + OWLv2）。
