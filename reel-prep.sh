#!/usr/bin/env bash
# reel-prep.sh <项目源文件夹> <工作目录>
# 机械前处理：逐词转写(仅一次) → 统一切割计划(重复重起+填充词+气口) → 采样级精确切割
#            + 时间映射 → voice_cut.srt(精确字幕, 不二次转写) → HEIC转PNG → 素材清单。
# 调参: REEL_PLAN_OPTS='--exclude s-e,s-e' / '--no-fillers' / '--min-gap 0.4' 重跑本步;
#       REEL_CUTS='s-e,s-e' 追加强制删除区间。校对 voice_cut.srt → 存成 voice_corrected.srt 再 reel-make。
# 失败即停。依赖: ffmpeg, ffprobe, whisper-cli, sips。
set -uo pipefail
SRCDIR="${1:?用法: reel-prep.sh <源文件夹> <工作目录>}"
WORK="${2:?缺工作目录}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="${REEL_WHISPER_MODEL:-$HOME/models/whisper/ggml-large-v3-turbo.bin}"
PV="ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1"
die(){ echo "❌ $1" >&2; exit 1; }
mkdir -p "$WORK"
command -v whisper-cli >/dev/null || die "缺 whisper-cli"
[ -f "$MODEL" ] || die "whisper 模型不存在: $MODEL (设 REEL_WHISPER_MODEL)"

# 选配音
if [ -n "${REEL_AUDIO:-}" ]; then m4a="$REEL_AUDIO"; [ -f "$m4a" ] || die "REEL_AUDIO 不存在"; else
  auds=$(find "$SRCDIR" -maxdepth 1 -type f \( -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.wav' \) | sort)
  [ -n "$auds" ] || { echo "⛔ 缺配音 → 无法进行, 进度表注明缺配音"; exit 2; }
  [ "$(printf '%s\n' "$auds" | wc -l | tr -d ' ')" = 1 ] || die "源目录有多个音频，无法确定哪条是配音，设 REEL_AUDIO 指定：
$auds"
  m4a="$auds"
fi

# 逐词转写(唯一一次, 词级时间戳=唯一真相源)
ffmpeg -y -i "$m4a" -ar 16000 -ac 1 "$WORK/orig16.wav" 2>"$WORK/ffmpeg.log" || { tail -20 "$WORK/ffmpeg.log"; die "解码失败"; }
whisper-cli -m "$MODEL" -f "$WORK/orig16.wav" -l zh -osrt -of "$WORK/words" --max-len 1 >"$WORK/whisper.log" 2>&1 || { tail -20 "$WORK/whisper.log"; die "逐词转写失败"; }

# 统一切割计划: 重复重起 + 填充词 + 气口(静音∩词间隙) → 一张删除区间表(原始时间轴)
echo "===== 切割计划(重复/填充词需人工核对; 气口收缩安全) ====="
/usr/bin/python3 "$DIR/build_cut_plan.py" "$WORK/words.srt" "$WORK/orig16.wav" \
  --out "$WORK/cut_plan.txt" ${REEL_ACCEPT:+--accept "$REEL_ACCEPT"} ${REEL_PLAN_OPTS:-} || die "切割计划失败"
echo "  重复/填充词默认只列候选不删：核对后 REEL_ACCEPT='r1,f2' 重跑；气口收缩自动执行；REEL_CUTS 可追加强制删除"

# 精确切割(采样级 atrim+concat) + 导出时间映射 keeps.json (REEL_CUTS 追加, 前导逗号会被忽略)
SPEC="$(cat "$WORK/cut_plan.txt")${REEL_CUTS:+,$REEL_CUTS}"
/usr/bin/python3 "$DIR/cut_ranges.py" "$m4a" "$WORK/voice_tight_48k.wav" "$SPEC" \
  --keeps "$WORK/keeps.json" --ar 48000 --ac 1 || die "精确切割失败"

# 时间映射: 词级时间戳平移到切后轴 → 精确字幕(校对参考, 与净音频逐帧对齐, 不二次转写)
/usr/bin/python3 "$DIR/remap_srt.py" "$WORK/words.srt" "$WORK/keeps.json" \
  --out "$WORK/voice_cut.srt" || die "时间映射失败"

# HEIC -> PNG
if find "$SRCDIR" -maxdepth 1 -type f -iname '*.heic' | grep -q .; then
  mkdir -p "$WORK/png"
  fail=0
  while IFS= read -r -d '' f; do
    b=$(basename "$f"); b="${b%.*}"; o="$WORK/png/$b.png"
    [ -f "$o" ] && [ "$o" -nt "$f" ] && continue          # 已有且比源新才复用
    sips -s format png "$f" --out "$o" >/dev/null 2>&1 || { echo "❌ 转PNG失败: $b" >&2; fail=1; }
  done < <(find "$SRCDIR" -maxdepth 1 -type f -iname '*.heic' -print0)
  [ "$fail" = 0 ] || die "有 HEIC 转换失败（见上），不能继续"
fi

echo "✅ 配音(原$($PV "$m4a")s → 净$($PV "$WORK/voice_tight_48k.wav")s) | 工作目录 $WORK"
echo "===== 精确字幕(voice_cut.srt, 与净音频逐帧对齐; 校对文字后另存为 voice_corrected.srt) ====="
cat "$WORK/voice_cut.srt"
echo "===== 图片(PNG；plan 里写完整文件名，含 .png) ====="; ls -1 "$WORK/png" 2>/dev/null || echo "(无)"
echo "===== 视频(时长/帧率) ====="
find "$SRCDIR" -maxdepth 1 -type f \( -iname '*.mov' -o -iname '*.mp4' \) -print0 | while IFS= read -r -d '' f; do
  printf '  %8ss  %-9s  %s\n' "$($PV "$f" 2>/dev/null||echo '?')" "$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 "$f" 2>/dev/null||echo '?')" "$(basename "$f")"
done
echo "PREP_DONE"
