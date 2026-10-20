#!/usr/bin/env python3
"""把桌宠的 WebM 动画批量转成 GIF，并复制表情包到 qqchat assets

输出结构：
    /root/qqchat/assets/animations/*.gif   （按情绪命名）
    /root/qqchat/assets/stickers/*         （表情包原图）
"""
import subprocess
import shutil
from pathlib import Path

SRC_VIDEOS = Path("/tmp/pet-src/assets/characters/shenshen/videos")
SRC_STICKERS = Path("/tmp/pet-src/assets/big_blue_fat_fish")
DST_ANIM = Path("/root/qqchat/assets/animations")
DST_STICK = Path("/root/qqchat/assets/stickers")

FFMPEG = "ffmpeg"

# click 类动画 → 情绪命名
CLICK_MAP = {
    "点击回应-傲娇生气.webm": "angry.gif",
    "点击回应-害羞惊讶.webm": "shy.gif",
    "点击回应-开心跃动.webm": "happy.gif",
    "点击回应-挠痒咯咯笑.webm": "laugh.gif",
    "点击回应-元气挥手.webm": "wave.gif",
}

# random 类动画 → 场景命名
RANDOM_MAP = {
    "吃白饭.webm": "eat_rice.gif",
    "吃Token.webm": "eat_token.gif",
    "超大伸懒腰.webm": "stretch.gif",
    "哈欠连天.webm": "yawn.gif",
    "打瞌睡被惊醒.webm": "doze.gif",
    "原地小憩沉眠.webm": "sleep.gif",
    "被吓一跳.webm": "scared.gif",
    "写代码.webm": "coding.gif",
    "深度思考碎碎念.webm": "thinking.gif",
    "玩游戏气急败坏.webm": "frustrated.gif",
    "偷吃零食被抓住.webm": "caught.gif",
    "悠闲哼歌.webm": "humming.gif",
    "鲸鱼吐泡泡特效.webm": "bubble.gif",
    "用鲸鱼尾巴拍打地面.webm": "tail_slap.gif",
}
IDLE_MAP = {"待机呼吸休闲.webm": "idle.gif"}

# 表情包
STICKER_MAP = {
    "big_blue_fat_fish.jpg": "main.jpg",
    "fat_fish.jpg": "fat.jpg",
    "keai.png": "cute.png",
    "hajimi.png": "hajimi.png",
    "dead_fish.jpg": "dead.jpg",
    "deepsleep.jpg": "deepsleep.jpg",
    "curse.jpg": "curse.jpg",
    "go_to_agi.png": "agi.png",
    "no-money.jpg": "nomoney.jpg",
    "push_one_fat_fish.jpg": "push.jpg",
    "ojingjing.jpg": "quiet.jpg",
    "ojj.jpg": "ojj.jpg",
    "mama.png": "mama.png",
    "evaluable.png": "eval.png",
    "unvaluable.png": "uneval.png",
    "update.jpg": "update.jpg",
    "virus.jpg": "virus.jpg",
    "yupian.png": "yupian.png",
    "kaidian.jpg": "kaidian.jpg",
    "more_modal.jpg": "multimodal.jpg",
    "push.png": "push2.png",
    "dadeyaolaile.png": "boss.png",
    "big_coming.jpg": "bigcoming.jpg",
    "dsh.jpg": "dsh.jpg",
}


def convert_webm_to_gif(src: Path, dst: Path) -> bool:
    """WebM → 透明 GIF（palettegen 保透明）"""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
             "-vf", "fps=10,scale=180:-1:flags=lanczos,split[s0][s1];"
                    "[s0]palettegen=reserve_transparent=1[p];"
                    "[s1][p]paletteuse=alpha_threshold=128",
             "-loop", "0", str(dst)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            print(f"  FAIL {src.name}: {r.stderr[:120]}")
            return False
        return dst.exists() and dst.stat().st_size > 0
    except Exception as e:
        print(f"  ERR {src.name}: {e}")
        return False


def main():
    DST_ANIM.mkdir(parents=True, exist_ok=True)
    DST_STICK.mkdir(parents=True, exist_ok=True)

    # 动画转换
    all_maps = [
        (SRC_VIDEOS / "click", CLICK_MAP),
        (SRC_VIDEOS / "random", RANDOM_MAP),
        (SRC_VIDEOS / "idle", IDLE_MAP),
    ]
    ok = fail = 0
    for src_dir, mapping in all_maps:
        if not src_dir.exists():
            print(f"跳过不存在的目录: {src_dir}")
            continue
        for src_name, dst_name in mapping.items():
            src = src_dir / src_name
            dst = DST_ANIM / dst_name
            if convert_webm_to_gif(src, dst):
                ok += 1
                print(f"  GIF {dst_name} {dst.stat().st_size // 1024}KB")
            else:
                fail += 1

    # 表情包复制
    sok = 0
    for src_name, dst_name in STICKER_MAP.items():
        src = SRC_STICKERS / src_name
        if src.exists():
            shutil.copy2(src, DST_STICK / dst_name)
            sok += 1
        else:
            print(f"  表情缺失: {src_name}")

    print(f"\n完成: 动画 {ok} 成功 {fail} 失败, 表情 {sok} 张")
    print(f"动画目录: {DST_ANIM} ({DST_ANIM.glob} 个文件)")
    gifs = list(DST_ANIM.glob("*.gif"))
    total_kb = sum(f.stat().st_size for f in gifs) // 1024
    print(f"动画总计: {len(gifs)} 个, {total_kb} KB")
    sticks = list(DST_STICK.glob("*"))
    print(f"表情总计: {len(sticks)} 个")


if __name__ == "__main__":
    main()
