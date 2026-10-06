#!/usr/bin/env python3
"""把 map-style 的 34 个场景渲染成成片：逐帧推镜 + 转场溶解 + 流线/光点动效。

用法：
  python src/render_film.py --list                 # 列出场景与时长
  python src/render_film.py --preview 7 12 0       # 只渲这几场（各取前 8 秒），出无声预览
  python src/render_film.py                        # 全片（配 audio + 字幕 + 章节）

时间轴以项目根目录的 timeline.json 为准，音轨固定取 中文解说.wav；
两者时长不一致时会直接报错，避免声画错位。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import time
import wave
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]     # 仓库根目录
SRC = Path(__file__).resolve().parent
DATA, OUT = ROOT / 'data', ROOT / 'output'
WORK = ROOT / 'work'                            # 渲染中间产物（不入库）
FILM = WORK / 'film'
FF = imageio_ffmpeg.get_ffmpeg_exe()

spec = importlib.util.spec_from_file_location('ms', SRC / 'map_style.py')
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)
STORY = ms.STORY


def load_timeline():
    tl = json.loads((DATA / 'timeline.json').read_text())
    assert len(tl['scenes']) == len(STORY), '时间轴场景数与 story.json 不一致'
    return tl


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), 'rb') as w:
        return w.getnframes() / w.getframerate()


def guard_av(tl):
    """声画对齐自检：时间轴总长必须与解说音轨一致。"""
    audio = OUT / '中文解说.wav'
    if not audio.exists():
        print('· 未找到 中文解说.wav，跳过声画对齐检查')
        return
    dur = wav_seconds(audio)
    if abs(dur - tl['duration']) > 1.0:
        raise SystemExit(
            f'声画不一致：timeline.json = {tl["duration"]:.2f}s，'
            f'中文解说.wav = {dur:.2f}s\n'
            '请先用 src/cloud_voice.py 的产物重新生成 timeline.json。')
    print(f'· 声画对齐 OK：{dur:.2f}s')


def encode_scene(i, tl, fps, force=False, limit=None, prev_map=None):
    """渲染单场到 work/film/scene-XX.mp4，返回该场的静态底图（供下一场溶解）。"""
    ctx = ms.build(i)
    seg = tl['scenes'][i]
    dur = float(seg['end'] - seg['start'])
    span = min(dur, limit) if limit else dur
    count = max(1, round(span * fps))
    dst = FILM / f'scene-{i:02}.mp4'
    if dst.exists() and not force:
        print(f'  skip {i:02}（已存在）')
        return ctx['base_map']

    part = FILM / f'scene-{i:02}.partial.mp4'
    log = open(WORK / f'encode-film-{i:02}.log', 'w')
    proc = subprocess.Popen(
        [FF, '-hide_banner', '-loglevel', 'warning', '-y',
         '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{ms.W}x{ms.H}',
         '-r', str(fps), '-i', '-', '-an',
         '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '6',
         '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(part)],
        stdin=subprocess.PIPE, stderr=log)
    t0 = time.time()
    try:
        for k in range(count):
            local = k / fps
            absolute = seg['start'] + local
            cap = next((c['text'] for c in seg['captions']
                        if c['start'] <= absolute < c['end']), '')
            im = ms.compose(ctx, prev_map, local, span, cap, absolute,
                            tl['duration'], i)
            proc.stdin.write(im.tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError('编码失败，见 ' + str(log.name))
        part.replace(dst)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        log.close()
    el = time.time() - t0
    print(f'  VIDEO {i:02} {STORY[i]["era"]}｜{STORY[i]["title"]}  '
          f'{span:6.1f}s / {count:5d} 帧  {el:5.1f}s（{count / max(el, 1e-6):.0f} fps）',
          flush=True)
    return ctx['base_map']


def concat(listing: Path, out: Path):
    subprocess.run([FF, '-hide_banner', '-loglevel', 'warning', '-y',
                    '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy',
                    str(out)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fps', type=int, default=15)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--preview', type=int, nargs='*', default=None)
    ap.add_argument('--limit', type=float, default=8.0, help='预览时每场截取的秒数')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    tl = load_timeline()
    if a.list:
        for i, (s, t) in enumerate(zip(STORY, tl['scenes'])):
            print(f'{i:02}  {t["end"] - t["start"]:6.1f}s  {s["era"]}｜{s["title"]}')
        print(f'合计 {tl["duration"]:.1f}s = {tl["duration"] / 60:.1f} 分钟')
        return

    FILM.mkdir(parents=True, exist_ok=True)

    if a.preview is not None:
        ids = a.preview or [7, 12, 15]
        print(f'· 预览模式：场景 {ids}，每场 {a.limit}s @ {a.fps}fps')
        prev = None
        for i in ids:
            prev = encode_scene(i, tl, a.fps, force=True, limit=a.limit, prev_map=prev)
        listing = FILM / 'preview.txt'
        listing.write_text('\n'.join(f"file 'scene-{i:02}.mp4'" for i in ids))
        out = FILM / '预览片段.mp4'
        concat(listing, out)
        print(f'· 预览写出 {out}')
        return

    guard_av(tl)
    print(f'· 全片渲染：{len(STORY)} 场 / {tl["duration"]:.1f}s @ {a.fps}fps')
    prev = None
    t0 = time.time()
    for i in range(len(STORY)):
        prev = encode_scene(i, tl, a.fps, force=a.force, prev_map=prev)
    listing = FILM / 'concat.txt'
    listing.write_text('\n'.join(f"file 'scene-{i:02}.mp4'" for i in range(len(STORY))))
    silent = FILM / '画面.mp4'
    concat(listing, silent)

    out = OUT / '中国五千年_山河流转_1080p.mp4'
    cmd = [FF, '-hide_banner', '-loglevel', 'warning', '-y',
           '-i', str(silent), '-i', str(OUT / '中文解说.wav')]
    maps = ['-map', '0:v:0', '-map', '1:a:0']
    if (OUT / '中文字幕.srt').exists():
        cmd += ['-i', str(OUT / '中文字幕.srt')]
        maps += ['-map', '2:s:0']
    if (OUT / '章节.ffmeta').exists():
        cmd += ['-i', str(OUT / '章节.ffmeta')]
        maps += ['-map_metadata', '3', '-map_chapters', '3']
    cmd += maps
    cmd += ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-ar', '44100']
    if (OUT / '中文字幕.srt').exists():
        cmd += ['-c:s', 'mov_text', '-metadata:s:s:0', 'language=zho']
    cmd += ['-metadata:s:a:0', 'language=zho',
            '-t', f'{tl["duration"]:.6f}', '-movflags', '+faststart', str(out)]
    subprocess.run(cmd, check=True)
    print(f'DONE {out}  用时 {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
