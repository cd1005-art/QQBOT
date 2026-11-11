"""生成 QQBOT 横屏宣传片（1920x1080 MP4）。

流程：TTS 解说 → 量出时长 → 按段落生成分镜图 → ffmpeg 合成（淡入淡出 + 音轨）。

依赖：Pillow、imageio-ffmpeg（提供 ffmpeg 二进制）、edge-tts（可选，失败则静音并给出提示）。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "promo"
W, H = 1920, 1080

BG = (14, 17, 22)
PANEL = (22, 27, 35)
LINE = (37, 44, 56)
TEXT = (231, 234, 242)
SUB = (152, 161, 179)
ACCENT = (79, 140, 255)
GREEN = (63, 191, 127)
WARN = (240, 180, 41)

FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


FFMPEG_LOCAL = Path("D:/AIWork/_tools/ffmpeg/bin/ffmpeg.exe")


def ffmpeg_exe() -> str:
    if FFMPEG_LOCAL.exists():
        return str(FFMPEG_LOCAL)
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def dur_of(path: Path) -> float:
    """用 ffmpeg -i 的 stderr 读时长，避免依赖 ffprobe。"""
    out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out.stderr)
    if not m:
        return 0.0
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


# ---------------- 解说词 ----------------

SCRIPT = [
    ("群里刷屏、广告、违规图，还在一个个手动处理？", "QQBOT，用官方接口做的群管理控制台", 0),
    ("不接协议端，不模拟客户端，全部走 QQ 开放平台。", "Webhook + Ed25519 验签，一条事件都不漏", 1),
    ("消息全量接收，撤回、禁言、全员禁言，点一下就执行。", "面板、命令行、定时任务，三处都能触发", 2),
    ("关键词命中，自动撤回加禁言；违规图片，机器视觉直接判断。", "规则、识别、审核，三个开关各管一件事", 3),
    ("还能定时发公告，@全体成员，让机器人看图聊天、按关键词找图发群。", None, 4),
    ("每一步操作都有记录，谁在什么时候动了什么，随时可查。", None, 5),
    ("开源免费，一条命令就能部署。", "github.com/cd1005-art/QQBOT", 6),
]

# 每个分镜对应的视觉元素：(标题, 副标题, 截图相对路径 或 None)
SCENES = [
    ("QQBOT", "QQ 官方机器人群管理控制台", "assets/landing.png",
     ["官方 Webhook 接入", "Ed25519 验签", "无第三方协议端"]),
    ("接入方式", "官方接口，事件不丢", "assets/panel.png",
     ["3 秒内响应平台校验", "原始事件全量落库", "重推自动去重"]),
    ("群里能做什么", "点一下就执行", "assets/panel.png",
     ["撤回 / 禁言 / 全员禁言", "@全体成员（官方标签）", "定时公告，多群可选"]),
    ("规则自动处置", "命中关键词就动手", "assets/panel.png",
     ["撤回 / 禁言 / 撤回+禁言", "关键词也可直接回复", "按群独立开关"]),
    ("图片能力", "看得懂，也管得住", "assets/docs.png",
     ["审核：违规即处置", "识别：看图回答", "找图：来张二次元图片"]),
    ("全程留痕", "三本账，随时可查", "assets/panel.png",
     ["事件留档", "操作审计", "处置记录"]),
    ("开源项目", "MIT License", "assets/landing.png",
     ["一条命令部署", "Docker / systemd", "github.com/cd1005-art/QQBOT"]),
]


# ---------------- 绘图 ----------------

def round_rect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def base_canvas() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # 顶部一条极淡的渐变条，避免纯色发闷
    for i in range(160):
        alpha = int(26 * (1 - i / 160))
        d.line([(0, i), (W, i)], fill=(BG[0] + alpha // 6, BG[1] + alpha // 5, BG[2] + alpha // 3))
    return img


def brand(d: ImageDraw.ImageDraw):
    d.text((96, 64), "QQBOT", font=font(30, True), fill=ACCENT)
    d.text((96 + 118, 70), "群管理控制台", font=font(22), fill=SUB)


def slide(title: str, sub: str, image: Path | None, bullets: list[str], index: int,
          caption: str = "") -> Image.Image:
    img = base_canvas()
    d = ImageDraw.Draw(img)
    brand(d)
    d.text((W - 150, 64), f"0{index + 1}", font=font(30, True), fill=LINE)

    ty = 176
    d.text((96, ty), title, font=font(66, True), fill=TEXT)
    if sub:
        d.text((96, ty + 92), sub, font=font(30), fill=SUB)

    top = ty + 190
    if image and image.exists():
        shot = Image.open(image).convert("RGB")
        max_h = 540
        max_w = int(W * 0.56)
        ratio = min(max_w / shot.width, max_h / shot.height)
        nw, nh = int(shot.width * ratio), int(shot.height * ratio)
        shot = shot.resize((nw, nh), Image.LANCZOS)

        pad = 10
        round_rect(d, (96 - pad, top - pad, 96 + nw + pad, top + nh + pad),
                   18, fill=PANEL, outline=LINE, width=2)
        img.paste(shot, (96, top))

        # 右侧要点栏
        bx = 96 + nw + 72
        by = top + 40
        for b in bullets:
            d.ellipse((bx, by + 12, bx + 12, by + 24), fill=ACCENT)
            d.text((bx + 30, by), b, font=font(30), fill=TEXT)
            by += 76
    else:
        by = top
        for b in bullets:
            d.ellipse((96, by + 12, 108, by + 24), fill=ACCENT)
            d.text((128, by), b, font=font(34), fill=TEXT)
            by += 84

    # 底部字幕条
    if caption:
        cy = H - 96
        d.line([(96, cy - 22), (96 + 64, cy - 22)], fill=ACCENT, width=3)
        d.text((96, cy), caption, font=font(28), fill=(200, 208, 222))

    return img


# ---------------- 语音 ----------------

VOICE = "zh-CN-YunxiNeural"
RATE = "+8%"


def tts(text: str, out: Path) -> bool:
    try:
        import edge_tts
    except ImportError:
        print("[tts] 未安装 edge-tts，跳过配音", file=sys.stderr)
        return False

    async def run():
        com = edge_tts.Communicate(text, VOICE, rate=RATE)
        await com.save(str(out))

    try:
        asyncio.run(run())
        return out.exists() and out.stat().st_size > 1000
    except Exception as exc:
        print(f"[tts] 合成失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


# ---------------- 合成 ----------------

def build(voice_ok: bool, total: float):
    ff = ffmpeg_exe()
    OUT.mkdir(exist_ok=True)
    clips = []
    # 每段时长按字数比例分配，最少 4 秒
    weights = [max(1, len(t)) for t, _, _ in SCRIPT]
    total_w = sum(weights)
    durs = [max(4.0, total * w / total_w) for w in weights]
    scale = total / sum(durs)
    durs = [d * scale for d in durs]

    for i, (title, sub, shot, bullets) in enumerate(SCENES):
        img = slide(title, sub, ROOT / "promo" / shot, bullets, i, SCRIPT[i][0])
        png = OUT / f"slide{i}.png"
        img.save(png)
        mp4 = OUT / f"clip{i}.mp4"
        d = durs[i]
        vf = (f"fade=t=in:st=0:d=0.45,fade=t=out:st={max(0, d - 0.45):.2f}:d=0.45,"
              f"format=yuv420p")
        subprocess.run([ff, "-y", "-loop", "1", "-framerate", "30", "-t", f"{d:.2f}",
                        "-i", str(png), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
                        "-crf", "20", "-pix_fmt", "yuv420p", str(mp4)],
                       check=True, capture_output=True)
        clips.append(mp4)
        print(f"[clip] {i} {d:.1f}s")

    # SRT 字幕
    def ts(t: float) -> str:
        h, m, sec = int(t // 3600), int(t % 3600 // 60), t % 60
        return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",")

    lines, acc = [], 0.0
    for i, (text, _, _) in enumerate(SCRIPT):
        lines.append(f"{i + 1}\n{ts(acc)} --> {ts(acc + durs[i])}\n{text}\n")
        acc += durs[i]
    (OUT / "QQBOT-promo.srt").write_text("\n".join(lines), encoding="utf-8")

    concat = OUT / "concat.txt"
    concat.write_text("\n".join(f"file '{c.as_posix()}'" for c in clips), encoding="utf-8")
    silent = OUT / "silent.mp4"
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-c", "copy", str(silent)], check=True, capture_output=True)

    final = OUT / "QQBOT-promo.mp4"
    if voice_ok and (OUT / "voice.mp3").exists():
        vdur = dur_of(silent)
        subprocess.run([ff, "-y", "-i", str(silent), "-i", str(OUT / "voice.mp3"),
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                        "-af", "apad", "-t", f"{vdur:.2f}",
                        "-movflags", "+faststart", str(final)],
                       check=True, capture_output=True)
    else:
        final.write_bytes(silent.read_bytes())
    print(f"[done] {final} {final.stat().st_size/1024/1024:.1f} MB, {dur_of(final):.1f}s")


def main():
    OUT.mkdir(exist_ok=True)
    narration = " ".join(t for t, _, _ in SCRIPT)
    voice = OUT / "voice.mp3"
    ok = tts(narration, voice)
    total = dur_of(voice) + 1.6 if ok else 34.0
    print(f"[audio] {'有配音' if ok else '无配音'} 时长 {total:.1f}s")
    build(ok, total)


if __name__ == "__main__":
    main()
