# reel 引擎（reelcore）

Mac 主控的视频装配工具。LLM（Claude）只做决策，感知与渲染交给确定性代码；远程 NVIDIA GPU 主机（下文统称 5090）是可选的渲染后端。

## 分层

| 层 | 位置 | 职责 | 产物 |
|---|---|---|---|
| 工具层 | `reel.py` → `reelcore/cli.py`（`bin/reel` 启动） | 每个子命令 = 一个工具，stdout 一个 JSON，失败 exit 1 + 结构化错误；参数错误 exit 2 + `ARG_INVALID` | `{"ok":…,"result"/"error":…}` |
| 决策层 | Claude 本身 | 看素材清单、带网格的帧、observation，写 `plan.json` | EditPlan v1 |
| 感知层 | `perceive.py` / `web.py` | 抽帧+坐标网格；登记 LLM 看图得到的框；OWLv2 文字找目标；网页读 DOM 逐字框 | `observations.json` |
| 计划层 | `plan.py` | EditPlan v1 校验（一次报全部错误）；旧文本 plan 适配 | 规范化 plan |
| 几何层 | `camera.py` | bbox → 输出比例取景窗 → 缓动 → `perspective` 亚像素四角表达式 | 逐帧取景 |
| 渲染层 | `render.py` | 帧级时间线（转场重叠计入）、片段、拼接、混音、调色、字幕 | 成片 |
| 验收层 | `verify.py` | 尺寸/帧率/帧数/音视频时长/音频采样率（成片固定 48 kHz）/可解码；任何一项不过即失败 | RenderResult |
| 远程层 | `remote.py` | 5090：探测、显存闸门、GPU 锁、rsync、远程执行 | — |

backend 只决定编码器和字幕烧录方式：Mac = libx264 + mpv（本机 ffmpeg 无 libass）；5090 = NVENC + libass。时间线数学只有一份。

## 安装与运行环境

需要 Python ≥ 3.9、`ffmpeg` / `ffprobe`、Pillow 和 NumPy。依赖装进专用 venv，`bin/reel` 放上 `PATH`：

```bash
git clone https://github.com/shuaige121/agent-reel ~/.claude/plugins/reel
python3 -m venv ~/.local/share/reel/venv
~/.local/share/reel/venv/bin/python -m pip install pillow numpy
export PATH="$HOME/.claude/plugins/reel/bin:$PATH"     # 写进 ~/.zshrc
reel doctor
```

- **`bin/reel`**：bash 启动脚本（兼容 bash 3.2，经符号链接调用也成立）。解释器顺序：`REEL_PYTHON`（非空则原样使用）→ `~/.local/share/reel/venv/bin/python` → `PATH` 里的 `python3`；然后 `exec` 引擎的 `reel.py`，参数、退出码和输出原样透传。它是脚本不是函数，所以在每次调用都是新 shell 的 agent Bash 里也能用。
- **`reel doctor`**：无参数，输出一个 JSON：`result.python` 是实际解释器，`result.checks[]` 每项 `name`、`ok`、`required`、`detail`、`fix`（`ok` 时 `fix` 为 `null`）。必需项：`python`（≥ 3.9）、`ffmpeg`、`ffprobe`、`pillow`、`numpy`；必需项缺失 → exit 1，`DEPENDENCY_MISSING`，`detail.missing` 列名字。可选项（只报告，不影响退出码）：`subtitles`（ffmpeg 有 `ass` 滤镜或有 `mpv`）、`playwright`、`whisper`（`whisper-cli` 和 `REEL_WHISPER_MODEL` 指向的模型）、`pypinyin`、`beats`（`REEL_VENV_PY` 或默认 venv 的 python）、`locate`（torch 与 transformers）、`remote`（是否设了 `REEL_REMOTE`，不发起网络连接）。
- **可选依赖**：`reel voice check` 核对中文（默认 `--lang zh`）另需 `pypinyin`，装进同一个 venv，缺了在调用 whisper 之前报 `BACKEND_UNAVAILABLE`（`detail.missing`、`detail.fix`）；`reel words` 只需 `whisper-cli` 和模型，不需要 `pypinyin`；`reel beats` / `reel snap` 默认用同一个 venv，`REEL_VENV_PY` 可改。
- **参数错误**：缺子命令、未知子命令、缺必需参数、非法取值，stdout 仍是一个 JSON：`{"ok": false, "error": {"code": "ARG_INVALID", "message": …, "detail": {"usage": …}}}`，exit 2；`-h` / `--help`（含子命令的）输出帮助文本，exit 0。

## 工作流（Claude 按这个顺序调用工具）

```bash
cd "$P" && mkdir -p preview                           # 在项目目录里跑：plan.json、observations.json、预览都放这里
reel inspect 素材/a.jpg 素材/b.mp4                    # 逐个列文件（素材/* 遇到非素材文件报 ASSET_UNREADABLE；zsh 里没有匹配的通配符会让整条命令失败）；视频附切镜点
reel frame 素材/a.jpg --out preview/a_grid.png        # 带 0–1 网格的帧 → Read 这张图读坐标
reel observe --obs observations.json --src 素材 --asset a.jpg --bbox 0.62,0.18,0.80,0.41 --label "产品 logo"
reel locate  --obs observations.json --src 素材 --asset b.mp4 --t 4.2,4.8,5.4,6.0 --query "a coffee machine" --check-png preview/chk.png
#   ↑ OWLv2，本机无 torch 时自动走 5090；--check-png 把检出框画回帧上，必须看一眼再用
#     同一时间点可能有多个同名框（误检），按时间序列里位置连贯的那条挑 id
# 写 plan.json（见下），zoom_to 引用 observation_id
reel validate plan.json                               # 省略 --obs 时读 plan 同目录的 observations.json（所有命令相同）
reel compile  plan.json                               # 帧级时间线，不渲染
reel preview  plan.json s2 --out preview/s2.mp4 --sheet preview/s2.png   # 单镜头无声预览 + 抽帧拼图
reel render   plan.json --out 成片.mp4 --sheet preview/final.png          # --where auto|mac|5090；成片音频固定 AAC 48 kHz，验收含采样率
```

旧引擎的 txt 分镜可转成 EditPlan：`reel from-legacy 分镜.txt --src 素材 --voice 配音.mp3 --out plan.json`（其余参数见 `--help`）。

`render --where auto`：5090 在线、有 NVENC+libass、闸门通过 → 远程出片并拉回，在 Mac 上再验收一次；否则回退本机并在结果 `note` 里说明原因。用户没有点名用 5090 时写 `--where mac`。

## EditPlan v1 要点

```json
{"version": "reel.plan.v1", "src_dir": "素材", "output": {"width": 1080, "height": 1920, "fps": 60},
 "duration_policy": "fit_last_shot",
 "shots": [
  {"id": "s1", "asset": "a.jpg", "duration": 3.2,
   "camera": [{"op": "zoom_to", "observation_id": "obs-1", "t0": 0.4, "t1": 3.0,
               "easing": "ease_in_out", "padding": 0.15, "max_zoom": 2.5}],
   "transition_out": {"type": "fade", "duration": 0.4}},
  {"id": "s2", "asset": "b.mp4", "source_in": 12.0, "duration": 2.5, "camera": []}],
 "audio": {"voice": "voice.mp3", "subtitles": "voice.vtt", "bgm": null, "bgm_gain": 0.16, "keywords": ["关键词"]},
 "look": {"grade": false, "lut": null}}
```

- `camera: []` = 真正静止。`zoom_to` 的框永远是原图归一化坐标，必须来自 observation（校验 IoU≥0.5，不许手改）。
- **视频镜头**：observation 的时间点必须在本镜头源区间内；区间跨切镜 → `SCENE_CUT_IN_SHOT`；
  源画面自己在动（首尾 SSIM<0.85）却只给单帧框 → `SOURCE_CAMERA_MOVING`，改用
  `"track": ["obs-5","obs-8",…]`（同一目标多个时间点，按时间递增）：取景窗尺寸取能容纳所有关键帧的最大值，中心在关键帧间线性跟随；带 track 的 zoom_to 必须是该镜头最后一个运镜。
- `move_to` 给精确取景窗；`space: "cover"` 时坐标相对「居中铺满窗」，旧 plan 的 in/out/pan 就用它表达。
- 主体按输出比例放不下（竖屏里的宽主体）→ `SUBJECT_TOO_LARGE`，不悄悄裁掉；只是 `padding` 留白放不下时自动缩小留白，不报错。
- 时间线以配音为准：可视帧数 = Σ镜头帧数 − Σ转场帧数。`require_match` 差 >0.1s 报 `DURATION_MISMATCH`；`fit_last_shot` 自动调整最后一镜。
- 视频镜头入点+时长超出源长 → `SOURCE_TOO_SHORT`（不再定格末帧）；差 ≤1.5 帧（时长取整误差）自动用末帧补齐。视频入点用输入端 seek（输出端 `-ss` 会让 perspective 的帧号从源第 0 帧数起，运镜错位）。
- 字幕按块严格解析：无法解析的块报 `SUBTITLES_INVALID`，不静默丢；验收核对每条字幕都落在成片时长内。
- `audio.voice` 必填；它和 `asset`、字幕、BGM 一样按 `src_dir` 解析，不是按 plan 所在目录。

## 网页镜头：一边讲、一边跟随、一边高亮（不做 OCR）

文字位置直接读 DOM（`Range.getClientRects`，逐字精确），镜头和高亮都由浏览器逐帧绘制（Playwright）：推近时浏览器按新缩放重新栅格化，字不虚；2 倍超采样截图再缩小。

```bash
reel words voice.mp3 --out words.json          # 配音 → 逐字时间（本机 whisper）；没有它就按字幕句内均分
reel voice check 样本.mp3 --script 试听文本.txt --brief brief.json   # 声音试听的 agent 签法：STT 错误率 ≤10%（中文按拼音）才写入 voice.approved
```
```json
{"id": "w1", "kind": "web", "duration": 28.8,
 "web": {"url": "https://… 或 article.html", "css": "（可选）隐藏弹窗/导航的 CSS"},
 "camera": [{"op": "follow"}],
 "highlight": [{"text": "页面上的原文，一字不差（空白可忽略）", "sync": "voice", "style": "marker"}]}
```
plan 顶层：`"output": {"width": 1080, "height": 1920, "fps": 60}`、`"duration_policy": "fit_last_shot"`、`"audio": {"voice": "voice.mp3", "words": "words.json"}`。配音按网页原文写，要高亮的句子必须与页面一字不差（空白和标点可忽略）。

- **排版宽度**：竖屏默认 390（手机排版，字大、整行完整，镜头只做纵向跟随）；横屏默认 1280（桌面排版，可推近到正文列）。`web.layout_width` 可覆盖。
- **follow**：取景宽度至少容纳整列文字（不切行首行尾）、水平锁定文字列；正在讲的行离开中间舒适区（`comfort`，默认 ±0.28 画面高）才启动一次余弦缓动，时长随距离加长，峰值速度 ≤ `max_speed`（默认 0.7 画面高/秒）；默认从第一句所在位置开始（`start: "top"` 可从页顶推入）。`zoom_to` + `text` 则是一次性推到某段。
- **highlight**：`marker` 荧光笔（按行连续变宽，精确到小数像素）/ `spotlight` 其余压暗 / `both`。进度曲线：文字字符起讲时刻做插值节点、标点不设节点（不瞬跳）、σ=0.1s 高斯低通、单调化、提前 0.08s。
- 选 5090 出片时，网页镜头先在 Mac 渲成视频片段再上传（5090 没有浏览器环境）。
- 报 `TEXT_NOT_FOUND`＝页面没有这段原文；`TEXT_NOT_IN_VOICE`＝配音里对不上。先核对原文再重跑。
- 需要登录的页面先另存为 HTML；弹窗和广告用 `web.css` 隐藏。60fps 渲染约 3–4 秒/每秒成片。出片后 Read 拼图，确认高亮跟着讲、镜头没切掉行。

## AI 观察与操作时间线（timeline / scrub / edit / preview --range / check）

四组工具共用同一份 `compile_plan` 结果；区间一律半开 `[start,end)`，单位为输出帧。

```bash
# 1. 读时间线：V1 镜头（起止帧直接取自编译器 frames/trans/starts、转场重叠、入出点、运镜类型、重叠的配音句子）
#    C1 字幕 cue / A1 配音句子（--detail words 列逐词）/ BGM；默认 JSON，--format text 给人读
reel timeline plan.json --format text [--range 12:20] [--detail summary|events|words]
# 2. 多轨图：缩略图（--render 取成片帧，否则按编译取景裁素材首帧；网页镜头文字占位）、字幕条、配音波形+句子、
#    BGM/转场标记、运镜速度曲线（ShotCamera.at 采样；pan=取景窗宽/秒，zoom=d ln(1/宽)/dt）；同名 .json sidecar 给事件 ID 与像素区间
reel timeline plan.json --png tl.png [--range 12:20] [--render 成片.mp4] [--width 1600] [--beats beats.json]   # --beats 加节拍轨，标出每个切点离拍多少 ms
#    抽帧：干净帧 + 标注帧（safe/caption/bbox）+ index.json（请求时间、实际帧号、活跃事件 ID）
reel scrub plan.json --cuts --render 成片.mp4 --overlays safe,caption,bbox --out frames/
reel scrub plan.json --at 3.2,7.5 --out frames/           # 无 --render：按编译取景从素材合成（不含字幕，转场按淡化近似）
reel scrub plan.json --range 12:20 --samples 8 --out frames/
# 3. 小粒度编辑：内存应用 → validate+compile → 成功才原子写回；失败不落盘（committed=false + 原错误码）
reel edit plan.json --base r3 --op '{"op":"trim","id":"k8","duration":6.0}'
reel edit plan.json --op '[{"op":"insert","shot":{...},"after":"k2"},{"op":"trim","id":"k3b","duration":5.0}]'  # 数组=原子批量
#    op：trim(duration/source_in) · move(index|before|after|swap) · insert(shot + index|before|after)
#        replace(asset，保留时长与运镜) · set-transition(type/duration，type=null 去掉) · set-camera(camera[, viewport, space])
reel history plan.json
reel undo plan.json --to r1                               # 提交一个内容等于 r1 的新 revision，历史不删
# 4. 区间带声低清预览（先按目标 fps 编译全片，整镜渲染保留转场 handles，再截区间降采样；配音/BGM 按全片同一时刻截取）
reel preview plan.json --range 12:20 [--size 540x960] [--fps 15] [--audio mix|voice|none] --out p.mp4
reel check 成片.mp4 --plan plan.json [--report check.json]
```

- **revision**：`.reel-history/`（与 plan 同目录）存每个 revision 的完整快照 `rN.json` 和 `log.json`（op、时间、hash）。第一次编辑把原文件登记为 r0。一个目录只对应一个 plan：同目录另一个 plan 编辑时报 `HISTORY_MISMATCH`。plan.json 被手改过 → 当前 revision 显示 `rN*`，`--base rN` 会报 `REV_CONFLICT`，下一次编辑先把手改版本登记为 `external_edit`。编辑结果返回 `changed_ids`、`content_changed_ids`、`shifted_ids`、`affected_frames` 与新的文字时间线；`ok` 只代表结构与编译通过，不代表渲染验收。
- **preview --range**：预览第 k 帧 = 全片时刻 `A + k/fps`（测试逐帧比对全片、音频按样本比对）；尺寸必须与输出同比例；字幕不烧录。单镜头无声预览 `preview plan.json s2 --out …` 保持原样。
- **check** 四态与闸门：见下一节「交付闸门」。`not_checked` 的项不算检查过；能不能交付看 `result.gate`，不看 `result.status`。
- 中文字体按 PingFang → Hiragino Sans GB → STHeiti → Arial Unicode → Noto CJK 查找，找不到时回退 PIL 默认字体并在 sidecar/index 里记录 `font_fallback`。

## 交付闸门（check / deliver / register / receipt，2026-10-09）

```bash
reel register brief.json                                  # 校验 brief，把交付目录交给 Bash guard 守
reel check 成片.mp4 [--plan plan.json] [--brief brief.json] [--profile default|social-vertical|项目.json] \
          [--reviews reviews.json] [--subs x.vtt]... [--text 封面.txt]... [--report check.json]
reel deliver 成片.mp4 --brief brief.json --version v3 [同 check 的参数] [--sample]
reel receipt <项目>/.reel/receipts/<名>-v3.json             # 交付文件还在、hash 没变
python3 hooks/guard_delivery.py / hooks/deliver_gate.py    # 由 ~/.claude/settings.json 挂成 PreToolUse(Bash) / Stop hook（hook 只用标准库，用系统 python3 即可）
```

reviews.json 的例子（`video_sha256` = `reel check` 结果里的 `video_sha256`；成片改了，记录自动作废）。brief.json 的模板在 SKILL.md。

```json
{"version": "reel.reviews.v1", "records": [
 {"video_sha256": "…", "check": "low_motion", "decision": "accept", "by": "user", "note": "片头标题停留是设计",
  "observations": [{"t": [0, 4.2], "note": "标题卡慢推，用户确认保留"}]},
 {"video_sha256": "…", "check": "final_watch", "decision": "accept", "by": "claude", "note": "完整看完，逐段对过分镜",
  "observations": [{"t": 0, "note": "首帧有标题"}, {"t": [12, 18], "note": "证据镜头对上口播"}]},
 {"video_sha256": "…", "check": "final_listen", "decision": "accept", "by": "user", "note": "用户从头听完",
  "quote": "声音可以，发吧"}]}
```

- **失败语义**：`reel check` 只有 gate 放行才 `ok:true`、exit 0；否则 `ok:false`、exit 1，`error.code` = `CHECK_FAILED`（`gate.blockers`）或 `CHECK_NEEDS_REVIEW`（`gate.pending_review`），`result` 仍是完整报告。阻断：任何 fail；任何检测出错（缺 ffmpeg、进程非零、事件行解析条数对不上、没跑到 `progress=end`）；profile `required` 里的项缺失或 `not_checked`；审片判定 reject。`--report` 写出的文件顶层就是报告：`items[]`（每项 `check`、`status`、`rule`、`measured`、`evidence`）、`counts`、`gate`。
- **检测项**：`decode_clean`、`format`（编码/像素格式/画幅/帧率/音频编码/采样率，按 profile）、`first_frame`（直接解第 0 帧）、`black_frames`、`freeze`（plan 感知）、`long_freeze`（>5 s 定格补长）、`low_motion`（4 s 以上几乎不动：字卡慢推/定格/静态画面 → needs_review）、`audio_clipping`、`loudness`、`head_silence`/`tail_silence`/`mid_silence`、`subtitles_in_range` 与 `verify.*`（需 --plan）、`text_scan`（brief 的可上屏字段、plan 可见文字、字幕、--subs/--text 的禁用词，规则见下）。`ocr_visible_text` 等固定 `not_checked`，列在 `gate.not_covered`。
- **profile**（`profiles/*.json`：`default`、`social-vertical`（竖屏社交平台）；`extends` 继承，字典合并、列表替换）：`required`、各项阈值与 `action`（fail / needs_review）、`audio`（required / silent / optional）、`text.banned/banned_words/review/extra_banned/allow/require`、`allow`（登记例外：`{check, t:[起,止], reason}`，违规证据全落在区间内 → pass 并记 `excused`）。`check` 与 `deliver` 都按 `--profile` > `brief.profile`（相对 brief 目录）> `default`。
- **文本扫描规则**（只覆盖输入文本，不读成片画面）：`text.banned` + `text.extra_banned` 是不分大小写的子串匹配，命中 → fail（默认含中文占位词 `待定`、`待确认`、`待补充`、`待填`、`占位` 和 `[FIELD]`）；`text.banned_words`（默认 `TODO`、`TBD`、`FIXME`）区分大小写、前后不紧邻字母数字下划线才算命中，命中 → fail，所以 `TODO:`、`(TBD)` 命中，`todo`、`TODOs`、`MY_TODO`、`mastodon` 不命中；`text.review`（默认 `placeholder`、`lorem ipsum`）不分大小写、同样的整词规则，词表里的空格匹配任意空白，命中 → `needs_review`（进 `pending_review: ["text_scan"]`，用 `check: "text_scan"` 的审片记录处理）。`text.allow` 对三类都生效。`banned_words`、`review` 必须是数组，否则 `PROFILE_INVALID`。evidence 每条带 `severity`（`fail` / `review`）。brief 只扫 `name`、`must_not_change[*]`，以及任意层级键名为 `text` / `title` / `caption` / `label` 的字符串；不扫 `version`、`language`、`deliverable`、`kind`、`profile`、`delivery_root` 和整个 `voice` 子树。
- **hook 的范围与局限**：Bash guard（`guard_delivery.py`）只守登记过的交付目录；reel 的写文件参数（`--out`、`--png`、`--sheet`、`--report`、`--check-png`）的 `--flag PATH` 和 `--flag=PATH` 两种写法都检查。它只解析命令文本，尽力而为：通配符、花括号展开、目标里的命令替换、脚本内部的写入、远程写入可能拦不住，由 Stop hook 和 `reel deliver` 的回执兜底。Stop hook（`deliver_gate.py`）默认只在会话 cwd 位于 `reel register` 登记的项目内、或本轮某个 Bash 命令调用过 reel（`reel …`、`bin/reel …`、`python3 …/reel.py …`；函数定义和 `which reel` 不算）时才判断，其余会话立即放行；`REEL_GATE_SCOPE=global` 恢复对本机所有会话生效。判断时回执要对应声明里点名的作品：已知作品名（登记表和交付流水里的 `name`，至少 2 个字符）出现在声明句里，则每个作品都要有同名的 final 回执，句中的版本号也要对上句中的作品；同一个 Bash 块里 `reel deliver` 之后又出片，则只出现在该块及更早块里的回执不算。
- **审片记录**（`reel.reviews.v1`）：每条绑定 `video_sha256`，`decision` accept/reject，`by`，`note`≥4 字；普通项要 `observations`（时间码 + 观察）；`final_listen` 由人签要 `quote`，由 agent 签（`by` 含 claude/agent 等）要 `observations`；回执 `approved_by` 记 `agent` / `human`（2026-11-18 起）。绑别的 hash 的记录作废（`stale_reviews`），缺字段的不算（`invalid_reviews`）。
- **brief**（`reel.brief.v1`）：`name`、`language`、`deliverable`、`delivery_root`、`must_not_change`、`voice`（tts/clone 要 `approved`：同一声音、试听样本 sha256；人批要原话 `quote`，agent 批要 `stt` 证据——`reel voice check 样本 --script 试听文本 --brief brief.json` 写入转写、原文和引擎，闸门按转写重算错误率 ≤10%（中文按无声调拼音，whisper 写错同音字不算念错），不信自报值，2026-11-18 起）。缺交付目录或目录太宽 → 不能交；缺其他 → 只能 `--sample`。
- **deliver**：每次重新检查实际文件（没有 `--report` 入口）→ 技术不过：样片也不交（`DELIVERY_BLOCKED`）→ 成片条件不满足且没 `--sample`：`DELIVERY_NOT_FINAL` → 复制到同目录临时文件、校验 sha256、硬链接落位不覆盖（`COPY_MISMATCH` / `VERSION_EXISTS`）→ 回执 `<项目>/.reel/receipts/<名>-<版本>.json` + 流水 `$REEL_STATE_DIR/deliveries.jsonl` + 登记表 `registry.json`（默认 `~/.local/state/reel/`）。交付文件权限按 umask（022 → 0644、077 → 0600），硬链接落位和 rename 回退都一样。回执只声明「已复制并校验」，不声称用户看过。

## MV：歌词时间源与验收契约（2026-11-16）

- `reel lyrics import 歌 --lyrics 歌词原件 --out x.lyrics.json [--srt 外部时间轴] [--words whisper.json] [--display sung|lyrics]`：时间源按「内嵌歌词流 → --srt → 报错 LYRICS_NO_TIMING」，不回退 whisper。按顺序和重复次数做全局对齐：漏唱/多唱（fail，不写文件）、改词（needs_review，默认显示唱的）、唱两遍（needs_review）、时间单调/不重叠/在歌内/每行 0.3–15 s（fail）；`--words` 只做交叉校验。`reel lyrics verify x.lyrics.json`：歌或歌词原件 hash 变了 → LYRICS_STALE；行被手改 → LYRICS_TAMPERED。
- `reel check / deliver … --contract mv.json`：契约 `reel.contract.v1`，不依赖 plan。字段：`songs`（路径 + sha256 + 起点 + policy，合集逐首按起点查）、`lyrics`（`reel lyrics import` 的输出）、`text_layer`（字层）、`output`（宽高帧率）、`release`（发布限制，只记录）；完整格式见 `reelcore/mvcheck.py` 文件头。可选 `effects`（用户要求叠加的音效层：path + sha256 + reason 原话；参照变为「原曲 + 音效层」，音效层每个有声 5 s 窗与原曲 |r| ≤ 0.3，改增益或换段伪装不过）。新增检查 `contract_files`、`av_duration_match`、`output_format`、`song_identity`（全长每 5 s 一窗查偏移、相关、电平；不足 5 s 的最后一窗也判，只有短于 0.1 s 的尾窗不判）、`song_level`（相对原曲比较响度和真峰值）、有歌词时再加 `lyrics_on_screen`、`lyrics_not_early`。
  - **歌词检查的范围**：这两项检查的是契约声明的歌词字层（`text_layer`）——每句演唱期间字层上有字、开唱前字层上没有字；不读成片画面的像素。把歌词画在字层之外的渲染器不在覆盖范围内。
  - **`song_level`**：单曲、`policy: "full"` 时整片对整首，`measured` 为 `mv_lufs / song_lufs / mv_peak_dbfs / song_peak_dbfs`；通过时把 default 的绝对响度/削波项记为 excused。合集或任何一首 `policy: "trim"` 时逐首比较：参照是原曲（`trim` 只取 `range`），成片区间是 `[at, at + 参照时长)`，每首的真峰值和响度与单曲用同一组容差，任何一首不过 → fail，`measured.songs[]` 逐首列出。任何一边可量区间短于 1 s 的那首判 fail。只有各首区间合计覆盖成片音频全长（缺口合计 ≤ 1 s）时，才免除 default 的绝对响度/削波项。
- 歌词重新导入失败（时间轴不合格）时不写文件，并删除同一路径上已有的输出；`LYRICS_CHECK_FAILED` 的 `detail` 有 `out`（路径）和 `removed_previous`（是否删除了旧文件），删除了旧文件时 message 写明「原有的 … 已删除」。契约必查项并进 gate 的 required，profile 去不掉；deliver 回执记 `hashes.contract`。

## 读 Shotcut 工程

```bash
reel mlt "工程.mlt" --video-only --range 0:60     # 文字时间线：每条轨道、片段起止、片段说明、源入出点、滤镜
reel mlt "工程.mlt" --dupes                       # 同一视频素材的源区间被用了两次（观众会看到同一段画面两次）
```
只读，不改工程。iCloud 未下载的 0 字节占位文件会明确报错。重复检测只查视频素材（图片/SVG 图层复用属正常）；**不同镜头拍同一动作**（例如两条不同素材都在揉纸）抓不到，需要画面比对（待做）。

## 节拍卡点

```bash
reel beats 音乐.mp3 --out beats.json            # 拍 / 强拍 / BPM；默认 auto：beat_this（真实音乐更稳），拍点过密或失败时回退 librosa
reel snap plan.json --beats beats.json --mode downbeat --tolerance 0.3   # 切点吸附到强拍；强拍被占用时退到普通拍
#   --min-gap 0.35：相邻切点至少隔 0.35s；--dry-run 只看报告不写入
reel undo plan.json --to r0                      # 卡点经 edit 写入，可撤销
```
- 模式：`beat` 每拍、`downbeat` 强拍、`every:N` 每 N 拍、`half` 半拍；`--offset` 为音乐在时间线上的起点。
- 卡点不会让镜头失效：镜头时长必须放得下运镜和手动高亮的 `t1`、两侧转场（各 2 倍）、视频镜头引用的 observation 时间点和源剩余长度。强拍都不行就退到普通拍，再不行保持原位（报告 `reason: constraint`；容差内没有拍则是 `no_beat`）。`require_match` 的 plan 卡点后总长不变。
- 强拍：beat_this 原始强拍按每 16 拍一段投票定小节相位，再每 4 拍取一个（规整、不多报）；输出 `downbeat_agreement`，<0.7 时 `needs_review`。
- 默认 beat_this：真实音乐上 librosa 会在段落里跳到反拍。卡点后用 `reel timeline plan.json --png tl.png --beats beats.json` 检查每个切点是否绿色（≤1 帧）。
- 依赖在 reel 自己的 venv：`~/.local/share/reel/venv`（Python 3.12：librosa、beat_this、torch CPU）；beat_this 模型缓存在 `~/.cache/torch/hub/checkpoints/beat_this-final0.ckpt`（81MB，首次下载很慢，可用 curl 断点续传）。

## 5090 规则（`remote.py` 强制执行）

- 主机：环境变量 `REEL_REMOTE`（ssh 目标，可空格分隔多个，选第一个在线的）；不设则只用本机。
- 显存默认 ≤80%，超过报 `NEED_USER_APPROVAL`：先告诉用户（要把显示器插集显或切无显示器 Ubuntu），同意后才加 `--allow-over-80`。GPU 利用率 ≥50% 报 `GPU_BUSY`，不抢别人的卡。
- 远程 flock `~/reel-remote/gpu.lock`，同一时间只一个 reel GPU 任务；拿到锁后再查一次显存（开跑前的估算可能已过期）。功耗上限是用户有意设的，不改。
- 每个 job 自带一份 reelcore 代码（`job/code/`），并发任务不会互相覆盖代码。
- 视觉环境：`reel remote setup-vision` 在 `~/reel-remote/venv-$(hostname -s)` 装 torch cu128 + transformers + OWLv2，不碰其他环境。每台主机一个（按 hostname 区分）。
- job 目录 `~/reel-remote/jobs/<时间>-<id>/` 不自动删除。
- `reel remote probe` 查主机和显卡状态。不重启、不关机。

## 旧命令行（兼容）

`engine_mac.py`（Mac/CPU）和 `engine_gpu.py`（在 5090 本机）参数与旧版相同，另外**必须给 `--src`**；运镜名 `still` 现在是真正静止，旧版的 1.04→1.08 缓推改叫 `drift`；关键词高亮改为 `--keywords`。

## 前处理（录音口播）

`reel-prep.sh <源目录> <工作目录>`：whisper 逐词转写 → `build_cut_plan.py` → `cut_ranges.py` → `remap_srt.py`。
气口收缩自动执行；**重复重起/填充词只列候选**（`cut_plan.txt.candidates.json`），核对后 `REEL_ACCEPT=r1,f2` 重跑才删。源目录有多条音频时必须 `REEL_AUDIO` 指定。

## 测试

测试要 import Pillow 和 NumPy，用 venv 的解释器运行（或先激活 venv）：

```bash
~/.local/share/reel/venv/bin/python tests/test_mv.py         # MV 契约与歌词：故障注入 + 对照
~/.local/share/reel/venv/bin/python tests/test_gate.py       # 交付闸门故障注入矩阵（check 失败语义/技术检测/文本扫描/brief 与试听/deliver 回执，约 2 分钟）
~/.local/share/reel/venv/bin/python tests/test_hooks.py      # 交付目录 Bash guard 与完成闸门（Stop hook）故障注入
~/.local/share/reel/venv/bin/python tests/test_reelcore.py   # 几何/跟踪表达式与 Python 轨迹逐帧一致、视频入点首帧、闸门、特殊字符路径、端到端渲染+验收；没有 Playwright / Chromium 时网页用例跳过并如实报告
~/.local/share/reel/venv/bin/python tests/test_output.py     # 渲染音频固定 48 kHz（含 verify 采样率项）、交付文件权限按 umask
~/.local/share/reel/venv/bin/python tests/test_mvcheck.py    # 合集与 trim 的逐首 song_level、尾窗、歌词重新导入的报错
~/.local/share/reel/venv/bin/python tests/test_textscan.py   # 禁用词三类规则、brief 只扫上屏字段
~/.local/share/reel/venv/bin/python tests/test_stopgate.py   # Stop hook 作用范围、块内顺序、回执对应点名的作品
~/.local/share/reel/venv/bin/python tests/test_bashguard.py  # Bash guard 识别 --out=PATH
~/.local/share/reel/venv/bin/python tests/test_cli.py        # reel doctor、bin/reel、参数错误的 JSON、pypinyin 预检
~/.local/share/reel/venv/bin/python tests/test_docs.py       # 文档：json 代码块合法、安装与范围说明
~/.local/share/reel/venv/bin/python tests/test_timeline.py   # timeline/scrub/edit/preview --range/check（合成素材，约 20s）
~/.local/share/reel/venv/bin/python tests/test_mlt.py        # 合成 MLT 的轨道/片段/转场/滤镜解析、0 字节占位、重复用料
~/.local/share/reel/venv/bin/python tests/test_beats.py      # 拍点/强拍精度、auto 回退、卡点吸附、成片切点落拍（worker 用 reel venv）
python3 test_pipeline.py         # 录音前处理：切割区间与时间映射
```

推镜抖动：perspective 路径推近 0.007px、横移 0.033px（旧 zoompan 0.15–0.34px，可察觉阈值约 0.15px）。根因：zoompan 的裁切原点是整数，且按色度子采样对齐（yuv420p 下步长 2 像素）；perspective 以 1/256 像素精度插值。
