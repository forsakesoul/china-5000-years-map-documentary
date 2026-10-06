#!/usr/bin/env python3
"""用云端 TTS（音色克隆）重新合成整片解说。

与 voice-clone.py 同构：产出到独立目录，不覆盖现有配音，确认满意后再切换。

  work/配音-cloud-<标签>/中文解说.wav   （22050Hz 单声道）
  work/配音-cloud-<标签>/timeline.json

支持的云端服务（--provider）：
  siliconflow  硅基流动 CosyVoice2-0.5B   https://api.siliconflow.cn
               需实名认证；国内直连；OpenAI 兼容；8 个中文预设 + 传参考音克隆
  minimax      MiniMax speech-02-hd       https://api.minimax.chat
               需 GroupId；音色库 300+；克隆质量更强

用法：
  # 1) 上传参考音，拿到音色 uri
  python src/cloud_voice.py --provider siliconflow --key sk-xxx \
         --ref 参考音/我的声音.wav --ref-text "参考音里念的那段文字"

  # 2) 试听（前 3 句）
  python src/cloud_voice.py --provider siliconflow --key sk-xxx --voice <uri> --audition

  # 3) 全片
  python src/cloud_voice.py --provider siliconflow --key sk-xxx --voice <uri> --tag sh-a

  # 其他
  python src/cloud_voice.py --provider siliconflow --key sk-xxx --list          # 列已有音色
  python src/cloud_voice.py --provider siliconflow --key sk-xxx --voice FunAudioLLM/CosyVoice2-0.5B:alex --audition

依赖：见仓库根目录 requirements.txt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]     # 仓库根目录
WORK = ROOT / 'work'                            # 中间产物（不入库）
DATA = ROOT / 'data'
STORY = json.loads((DATA / 'story.json').read_text())
SR = 22050          # 最终输出采样率（与 generate.py 一致）
BUF_SR = 44100      # 云端口径采样率，最后一次性转回 SR
FPS = 12
TIMEOUT = 120
VOICE_CACHE = WORK / 'cloud-voices.json'


def sentences_of(text: str):
    return re.findall(r'[^。！？]+[。！？]?', text)


def write_wav(path: Path, x: np.ndarray, sr: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(sr)
        o.writeframes((np.clip(x, -1, 1) * 32767).astype('<i2').tobytes())


def read_wav(path: Path):
    with wave.open(str(path), 'rb') as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
    return x, sr


def load_voice_cache() -> dict:
    if VOICE_CACHE.exists():
        try:
            return json.loads(VOICE_CACHE.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_voice_cache(d: dict):
    VOICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    VOICE_CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def safe_name(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]+', '-', s).strip('-') or 'voice'


# --------------------------------------------------------------------------- #
# SiliconFlow（硅基流动）——CosyVoice2-0.5B，OpenAI 兼容
# --------------------------------------------------------------------------- #
class SiliconFlow:
    name = 'siliconflow'
    base = 'https://api.siliconflow.cn/v1'
    model = 'FunAudioLLM/CosyVoice2-0.5B'
    max_ref_seconds = 30

    def __init__(self, key: str, speed: float = 1.0):
        self.h = {'Authorization': f'Bearer {key}'}
        self.speed = speed
        self.sess = requests.Session()

    def upload(self, ref: Path, ref_text: str, name: str) -> str:
        data = {'model': self.model, 'customName': safe_name(name), 'text': ref_text}
        with ref.open('rb') as f:
            r = self.sess.post(f'{self.base}/uploads/audio/voice',
                               headers=self.h,
                               files={'file': (ref.name, f, 'audio/mpeg')},
                               data=data, timeout=TIMEOUT)
        self._raise(r)
        return r.json()['uri']

    def list_voices(self):
        r = self.sess.get(f'{self.base}/audio/voice/list', headers=self.h, timeout=TIMEOUT)
        self._raise(r)
        return r.json()

    def synth(self, text: str) -> np.ndarray:
        body = {'model': self.model, 'input': text, 'voice': self.voice,
                'response_format': 'wav', 'sample_rate': BUF_SR, 'speed': self.speed}
        for attempt in range(4):
            try:
                r = self.sess.post(f'{self.base}/audio/speech', headers=self.h,
                                   json=body, timeout=TIMEOUT)
                if r.status_code in (429, 500, 502, 503):
                    raise RuntimeError(f'HTTP {r.status_code}')
                self._raise(r)
                import io
                x, sr = read_wav_bytes(r.content, io)
                return resample(x, sr, BUF_SR)
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    raise
                print(f'    retry {attempt+1}: {type(exc).__name__} {str(exc)[:70]}', flush=True)
                time.sleep(2 + 2 * attempt)
        raise RuntimeError('unreachable')

    @staticmethod
    def _raise(r):
        if r.status_code != 200:
            raise RuntimeError(f'HTTP {r.status_code}: {r.text[:300]}')


def read_wav_bytes(blob: bytes, io_mod):
    """解析服务端返回的 WAV 字节流（内存内）。"""
    with io_mod.BytesIO(blob) as f:
        with wave.open(f, 'rb') as w:
            sr = w.getframerate()
            n_ch = w.getnchannels()
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
    if sw != 2:
        raise RuntimeError(f'unsupported sample width {sw}')
    x = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    if n_ch > 1:
        x = x.reshape(-1, n_ch).mean(axis=1)
    return x, sr


TARGET_RMS = 0.09      # 逐句目标 RMS，用于抹平云端 TTS 的句子间电平差异
PEAK_CEIL = 0.97


def normalize_pcm(pcm: np.ndarray, target: float = TARGET_RMS) -> np.ndarray:
    """把单句电平对齐到统一 RMS，再限峰。

    注意：不能只用「峰值超 0.99 就压下来」——各家 TTS 的原始峰值差异很大
    （实测 bella 峰值在 0.50~0.99 间飘、活跃区 RMS 只有 charles 的 1/4），
    只压峰会让整片忽大忽小。这里改成按活跃区 RMS 匹配。
    """
    if pcm.size == 0:
        return pcm
    H = max(1, int(0.02 * BUF_SR))
    n = len(pcm) // H
    if n >= 2:
        fr = np.sqrt((pcm[:n * H].reshape(n, H) ** 2).mean(axis=1))
        thr = max(fr.max() * 0.08, 1e-5)
        act = fr > thr
        rms = float(np.sqrt((pcm[:n * H].reshape(n, H)[act] ** 2).mean())) if act.any() \
            else float(np.sqrt((pcm ** 2).mean()))
    else:
        rms = float(np.sqrt((pcm ** 2).mean()))
    gain = min(max(target / max(rms, 1e-9), 10 ** (-15 / 20)), 10 ** (15 / 20))
    out = pcm * gain
    peak = float(np.abs(out).max())
    if peak > PEAK_CEIL:
        out = out * (PEAK_CEIL / peak)
    return out


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    n = int(round(len(x) * sr_out / sr_in))
    idx = np.arange(n) * (sr_in / sr_out)
    i0 = np.floor(idx).astype(np.int64)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    frac = (idx - i0).astype(np.float32)
    return (x[i0] * (1 - frac) + x[i1] * frac).astype(np.float32)


# --------------------------------------------------------------------------- #
# MiniMax 官方 API
# --------------------------------------------------------------------------- #
class MiniMax:
    name = 'minimax'
    base = 'https://api.minimax.chat/v1'
    model = 'speech-02-hd'
    max_ref_seconds = None

    def __init__(self, key: str, group_id: str, speed: float = 1.0, pitch: int = 0):
        self.h = {'Authorization': f'Bearer {key}'}
        self.gid = group_id
        self.speed, self.pitch = speed, pitch
        self.sess = requests.Session()

    def upload(self, ref: Path, ref_text: str, name: str) -> str:
        with ref.open('rb') as f:
            r = self.sess.post(f'{self.base}/files/upload',
                               headers=self.h,
                               params={'GroupId': self.gid},
                               files={'file': (ref.name, f, 'audio/mpeg')},
                               data={'purpose': 'voice_clone'}, timeout=TIMEOUT)
        self._raise(r)
        file_id = r.json().get('file', {}).get('file_id') or r.json().get('file_id')
        vid = safe_name(name)[:40]
        if len(vid) < 8:
            vid = (vid + '-clonevoice')[:40]
        r2 = self.sess.post(f'{self.base}/voice_clone',
                            headers={**self.h, 'Content-Type': 'application/json'},
                            params={'GroupId': self.gid},
                            json={'file_id': file_id, 'voice_id': vid}, timeout=TIMEOUT)
        self._raise(r2)
        return vid

    def synth(self, text: str) -> np.ndarray:
        body = {'model': self.model, 'text': text, 'stream': False,
                'voice_setting': {'voice_id': self.voice, 'speed': self.speed,
                                  'vol': 1.0, 'pitch': self.pitch},
                'audio_setting': {'sample_rate': 32000, 'bitrate': 128000, 'format': 'wav'}}
        for attempt in range(4):
            try:
                r = self.sess.post(f'{self.base}/t2a_v2',
                                   headers={**self.h, 'Content-Type': 'application/json'},
                                   params={'GroupId': self.gid},
                                   json=body, timeout=TIMEOUT)
                self._raise(r)
                j = r.json()
                if 'data' not in j or not j['data'].get('audio'):
                    raise RuntimeError(f"bad response: {str(j)[:200]}")
                import io
                x, sr = read_wav_bytes(bytes.fromhex(j['data']['audio']), io)
                return resample(x, sr, BUF_SR)
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    raise
                print(f'    retry {attempt+1}: {type(exc).__name__} {str(exc)[:70]}', flush=True)
                time.sleep(2 + 2 * attempt)
        raise RuntimeError('unreachable')

    @staticmethod
    def _raise(r):
        if r.status_code != 200:
            raise RuntimeError(f'HTTP {r.status_code}: {r.text[:300]}')
        j = r.json()
        base = j.get('base_resp') or {}
        if base.get('status_code') not in (0, None):
            raise RuntimeError(f"minimax error {base.get('status_code')}: {base.get('status_msg')}")


# --------------------------------------------------------------------------- #
def make_synth(args):
    if args.provider == 'siliconflow':
        return SiliconFlow(args.key, speed=args.speed)
    if args.provider == 'minimax':
        if not args.group_id:
            sys.exit('--provider minimax 需要 --group-id')
        return MiniMax(args.key, args.group_id, speed=args.speed, pitch=args.pitch)
    sys.exit(f'未知 provider: {args.provider}')


def prepare(engine, outdir: Path):
    """逐句合成，按 44100 拼装到临时文件，最后一次转 22050。"""
    outdir.mkdir(parents=True, exist_ok=True)
    tmp_hi = WORK / f'.tmp-{outdir.name}-{BUF_SR}.wav'
    cache = WORK / 'cloud-voice-cache'
    cache.mkdir(parents=True, exist_ok=True)
    tag = outdir.name

    timeline = {'scenes': [], 'captions': [], 'sample_rate': SR,
                'voice': engine.voice, 'provider': engine.name, 'buffer_sr': BUF_SR}
    sample = 0
    out = wave.open(str(tmp_hi), 'wb')
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(BUF_SR)
    silence = lambda n: out.writeframes(b'\0\0' * max(0, n))  # noqa: E731

    total = sum(len(sentences_of(s['text'])) for s in STORY)
    done = 0
    for i, scene in enumerate(STORY):
        start = sample / BUF_SR
        local = []
        gap = round(BUF_SR * .55)
        silence(gap); sample += gap
        for sent in sentences_of(scene['text']):
            key = hashlib.md5(f'{tag}|{engine.voice}|{sent}'.encode()).hexdigest()[:10]
            dst = cache / f'{tag}-{key}.wav'
            if dst.exists():
                pcm, _ = read_wav(dst)
            else:
                pcm = normalize_pcm(engine.synth(sent))
                write_wav(dst, pcm, BUF_SR)
            done += 1
            a = sample / BUF_SR
            out.writeframes((np.clip(pcm, -1, 1) * 32767).astype('<i2').tobytes())
            sample += len(pcm)
            b = sample / BUF_SR
            cap = {'start': a, 'end': b, 'text': sent, 'scene': i}
            timeline['captions'].append(cap)
            local.append(cap)
            gap = round(BUF_SR * .18)
            silence(gap); sample += gap
            print(f'TTS {done}/{total}  {sent[:18]}  {b-a:.1f}s', flush=True)
        gap = round(BUF_SR * .55)
        silence(gap); sample += gap
        target = math.ceil(sample / BUF_SR * FPS) * BUF_SR // FPS
        if target < sample:
            target += BUF_SR // FPS
        silence(target - sample); sample = target
        timeline['scenes'].append({'start': start, 'end': sample / BUF_SR,
                                   'text': scene['text'], 'captions': local})
        print(f'AUDIO {i+1}/{len(STORY)}  {sample/BUF_SR:.1f}s', flush=True)
    out.close()

    final = outdir / '中文解说.wav'
    subprocess.run(['afconvert', '-f', 'WAVE', '-d', f'LEI16@{SR}', '-c', '1',
                    str(tmp_hi), str(final)], check=True, capture_output=True)
    timeline['duration'] = sample / BUF_SR
    (outdir / 'timeline.json').write_text(json.dumps(timeline, ensure_ascii=False, indent=2))
    return timeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--provider', default='siliconflow', choices=['siliconflow', 'minimax'])
    ap.add_argument('--key', default=None, help='API Key；也可用环境变量 VOICE_API_KEY')
    ap.add_argument('--group-id', default=None, help='MiniMax 专用')
    ap.add_argument('--voice', default=None, help='音色 uri / id；preset 也可（如 FunAudioLLM/CosyVoice2-0.5B:alex）')
    ap.add_argument('--ref', default=None, help='参考音频文件，用于上传克隆')
    ap.add_argument('--ref-text', default=None, help='参考音频里念的文字（必须完全一致）')
    ap.add_argument('--ref-name', default=None, help='音色名称')
    ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--pitch', type=int, default=0, help='仅 MiniMax')
    ap.add_argument('--tag', default=None)
    ap.add_argument('--list', action='store_true', help='列出已有自定义音色')
    ap.add_argument('--audition', action='store_true', help='只合成前 3 句试听')
    ap.add_argument('--sentence', default=None, help='只合成指定一句')
    args = ap.parse_args()

    import os
    args.key = args.key or os.environ.get('VOICE_API_KEY')
    if not args.key:
        sys.exit('缺少 --key（或环境变量 VOICE_API_KEY）')

    engine = make_synth(args)

    if args.list:
        print(json.dumps(engine.list_voices(), ensure_ascii=False, indent=2))
        return

    # 上传参考音 → 拿音色 id
    if args.ref:
        ref = (ROOT / args.ref) if not Path(args.ref).is_absolute() else Path(args.ref)
        if not ref.exists():
            sys.exit(f'参考音不存在：{ref}')
        x, sr = read_wav_is_playable(ref)
        dur = len(x) / sr
        print(f'参考音 {ref.name}  {dur:.2f}s @ {sr}Hz')
        if engine.max_ref_seconds and dur > engine.max_ref_seconds:
            sys.exit(f'参考音超过 {engine.max_ref_seconds}s 上限，请截短')
        if not args.ref_text:
            sys.exit('上传克隆必须提供 --ref-text（参考音里念的文字，需完全一致）')
        name = args.ref_name or ref.stem
        print('上传中 ...', flush=True)
        vid = engine.upload(ref, args.ref_text, name)
        cache = load_voice_cache()
        cache.setdefault(engine.name, {})[name] = {'voice': vid, 'ref': str(ref),
                                                   'ref_text': args.ref_text}
        save_voice_cache(cache)
        print(f'\n✅ 音色已创建，voice = \n{vid}\n')
        print(f'（已记录到 {VOICE_CACHE.relative_to(ROOT)}）')
        if not args.voice:
            args.voice = vid

    if not args.voice:
        sys.exit('缺少 --voice（音色 id/uri），或用 --ref 上传新音色')

    engine.voice = args.voice
    tag = args.tag or f'{engine.name}-{safe_name(args.voice.split(":")[1] if ":" in args.voice else args.voice)[:16]}'

    if args.sentence:
        x = engine.synth(args.sentence)
        dst = WORK / f'试听-{tag}-单句.wav'
        write_wav(dst, x, BUF_SR)
        print('写出', dst, f'{len(x)/BUF_SR:.1f}s')
        return

    if args.audition:
        sents = sentences_of(STORY[0]['text'])[:3]
        chunks = []
        for s in sents:
            c = engine.synth(s)
            chunks.append(np.concatenate([c, np.zeros(round(BUF_SR * .3), dtype=np.float32)]))
        y = np.concatenate(chunks)
        dst = WORK / f'试听-cloud-{tag}.wav'
        write_wav(dst, y, BUF_SR)
        print('写出', dst, f'{len(y)/BUF_SR:.1f}s')
        return

    outdir = WORK / f'配音-cloud-{tag}'
    tl = prepare(engine, outdir)
    print(f'\n完成：{len(tl["captions"])} 句，时长 {tl["duration"]:.2f} 秒 ({tl["duration"]/60:.1f} 分钟)')
    print('输出目录：', outdir)


def read_wav_is_playable(p: Path):
    """读参考音（任意 afconvert 支持的格式），返回 (mono float, sr)。"""
    if p.suffix.lower() == '.wav':
        x, sr = read_wav(p)
        return x, sr
    tmp = Path('/tmp') / f'ref-{hashlib.md5(str(p).encode()).hexdigest()[:8]}.wav'
    subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16@44100', '-c', '1',
                    str(p), str(tmp)], check=True, capture_output=True)
    return read_wav(tmp)


if __name__ == '__main__':
    main()
