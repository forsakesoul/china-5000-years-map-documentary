#!/usr/bin/env python3
"""对 配音-* 目录做母带处理：逐句响度对齐 + EBU R128 整体响度规整。

解决的问题：云端 TTS 每条句子返回的原始电平不一致（实测 bella 峰值在 0.50–0.99 间飘、
活跃区 RMS 只有 charles 的 1/4），直接拼出来会忽大忽小。

处理链：
  1. 逐句（按 timeline.json 的 caption 区间）算 RMS，匹配到统一目标电平（增益限幅 ±15dB）
  2. 单句限峰，避免削波
  3. 整轨 ffmpeg loudnorm 两遍（EBU R128，默认 -16 LUFS / TP -1.5）

用法：
  python src/audio_master.py work/配音-cloud-bella
  python src/audio_master.py work/配音-cloud-bella --lufs -15 --peak -1.0
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # noqa: BLE001
    FFMPEG = 'ffmpeg'

TARGET_RMS = 0.09          # 逐句目标 RMS（约 -21 dBFS，语音的常见说话电平）
MAX_GAIN_DB = 15.0
PEAK_CEIL = 0.97


def read_wav(p: Path):
    with wave.open(str(p), 'rb') as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
    return x, sr


def write_wav(p: Path, x: np.ndarray, sr: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), 'wb') as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(sr)
        o.writeframes((np.clip(x, -1, 1) * 32767).astype('<i2').tobytes())


def db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


def level_match(x: np.ndarray, caps, sr: int, target=TARGET_RMS, max_gain_db=MAX_GAIN_DB):
    """按 caption 区间逐句做增益匹配，返回 (y, 统计表)。"""
    y = x.copy()
    stats = []
    for i, c in enumerate(caps):
        a, b = int(round(c['start'] * sr)), int(round(c['end'] * sr))
        a, b = max(0, a), min(len(y), b)
        if b - a < int(0.05 * sr):
            continue
        seg = y[a:b]
        # 用活跃帧估电平，避免句内停顿拉低 RMS
        H = max(1, int(0.02 * sr))
        n = len(seg) // H
        if n == 0:
            continue
        fr = np.sqrt((seg[:n * H].reshape(n, H) ** 2).mean(axis=1))
        thr = max(fr.max() * 0.08, 1e-5)
        act = fr > thr
        rms = float(np.sqrt((seg[:n * H].reshape(n, H)[act] ** 2).mean())) if act.any() else float(np.sqrt((seg ** 2).mean()))
        gain = min(max(target / max(rms, 1e-9), 10 ** (-max_gain_db / 20)), 10 ** (max_gain_db / 20))
        seg2 = seg * gain
        pk = float(np.abs(seg2).max())
        if pk > PEAK_CEIL:
            seg2 *= PEAK_CEIL / pk
        y[a:b] = seg2
        stats.append((i, db(rms), db(gain), float(np.abs(seg2).max())))
    return y, stats


def loudnorm(src: Path, dst: Path, lufs: float, tp: float, lra: float = 11.0):
    """两遍 loudnorm（EBU R128）。"""
    probe = subprocess.run(
        [FFMPEG, '-hide_banner', '-nostats', '-i', str(src),
         '-af', f'loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json',
         '-f', 'null', '-'],
        capture_output=True, text=True)
    txt = probe.stderr
    i = txt.rfind('{')
    j = txt.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError('loudnorm 第一遍未返回 JSON：\n' + txt[-1500:])
    m = json.loads(txt[i:j + 1])
    af = (f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:"
          f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true:print_format=summary")
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src),
                    '-af', af, '-ar', '44100', str(dst)], check=True)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir', help='配音目录，如 work/配音-cloud-bella')
    ap.add_argument('--lufs', type=float, default=-16.0, help='整体目标响度 (LUFS)')
    ap.add_argument('--peak', type=float, default=-1.5, help='真峰值上限 (dBTP)')
    ap.add_argument('--target-rms', type=float, default=TARGET_RMS)
    ap.add_argument('--in-place', action='store_true', help='直接覆盖 中文解说.wav（先自动备份）')
    a = ap.parse_args()

    d = Path(a.dir)
    if not d.is_absolute():
        d = Path(__file__).resolve().parents[1] / d
    src = d / '中文解说.wav'
    if not src.exists():
        raise SystemExit(f'找不到 {src}')
    tl = json.loads((d / 'timeline.json').read_text())
    x, sr = read_wav(src)
    before_rms = float(np.sqrt((x ** 2).mean()))

    y, stats = level_match(x, tl['captions'], sr, target=a.target_rms)
    after_rms = float(np.sqrt((y ** 2).mean()))
    gains = np.array([s[2] for s in stats])
    print(f'逐句增益匹配：{len(stats)} 句')
    print(f'  增益范围 {gains.min():+.1f} ~ {gains.max():+.1f} dB，中位 {np.median(gains):+.1f} dB，'
          f'跨度 {gains.max()-gains.min():.1f} dB')
    print(f'  整轨 RMS {before_rms:.4f} -> {after_rms:.4f}')

    # 逐句增益后的句子电平离散度
    post = []
    for (i, rms0, g, pk) in stats:
        post.append(rms0 + g)
    post = np.array(post)
    print(f'  逐句电平 处理前 σ={np.std([s[1] for s in stats]):.2f} dB -> 处理后 σ={np.std(post):.2f} dB')

    flat = d / '中文解说-逐句对齐.wav'
    write_wav(flat, y, sr)

    master = d / '中文解说-master.wav'
    m = loudnorm(flat, master, a.lufs, a.peak)
    print(f'\nloudnorm: 输入 {m["input_i"]} LUFS / TP {m["input_tp"]} dBTP -> 目标 {a.lufs} LUFS / {a.peak} dBTP')
    print(f'  输出 {master}')

    z, zsr = read_wav(master)
    print(f'  校验：输出 RMS {np.sqrt((z**2).mean()):.4f}  峰值 {np.abs(z).max():.4f} ({db(float(np.abs(z).max())):.2f} dBFS)')
    clip = int((np.abs(z) >= 0.999).sum())
    print(f'  削波样本 {clip} 个')

    if a.in_place:
        bak = d / '中文解说-原始.wav'
        if not bak.exists():
            shutil.copy2(src, bak)
            print(f'  已备份原文件 -> {bak.name}')
        shutil.copy2(master, src)
        print(f'  已覆盖 {src.name}')


if __name__ == '__main__':
    main()
