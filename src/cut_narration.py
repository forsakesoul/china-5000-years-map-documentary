"""一次性脚本：从解说音轨和时间轴里剪掉画面已表达的 5 句取舍说明，后续时间整体前移。
不重新配音；同步更新 data/timeline.json、data/story.json 与 output/中文解说.wav，
字幕与章节随后由 src/export_text.py 重出。

这是一次性脚本：只有在用 TTS 重新合成旁白（旁白会带回被剪的句子）之后才需要重跑。
备份原始文件后再运行：
  python src/cut_narration.py
"""
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]     # 仓库根目录
DATA, OUT = ROOT / 'data', ROOT / 'output'
sys.path.insert(0, str(Path(__file__).resolve().parent))
from map_scenes import SCENES  # noqa: E402

tl = json.loads((DATA / 'timeline.json').read_text())
story = json.loads((DATA / 'story.json').read_text())

# 1) 算出要剪的区间（秒）
cuts = []
for i, (sc, cfg) in enumerate(zip(tl['scenes'], SCENES)):
    keys = cfg.get('skip_caption') or []
    cs = sc['captions']
    for k, c in enumerate(cs):
        if not any(key in c['text'] for key in keys):
            continue
        if k + 1 < len(cs):
            cuts.append((c['start'], cs[k + 1]['start'], i, c['text']))
        else:                       # 场景最后一句：从上一句结尾剪到本句结尾，保留场景尾巴
            cuts.append((cs[k - 1]['end'], c['end'], i, c['text']))
assert len(cuts) == 5, cuts
total_cut = sum(b - a for a, b, _, _ in cuts)


def shift(t):
    """原时间 → 剪后时间（落在被剪区间内的点不会被用到）"""
    return t - sum(b - a for a, b, _, _ in cuts if b <= t + 1e-9)


# 2) 音频：按采样点剪
with wave.open(str(OUT / '中文解说.wav'), 'rb') as w:
    params, sr = w.getparams(), w.getframerate()
    width = w.getsampwidth() * w.getnchannels()
    pcm = w.readframes(w.getnframes())
keep, pos = [], 0
for a, b, _, _ in cuts:
    sa, sb = round(a * sr), round(b * sr)
    keep.append(pcm[pos * width:sa * width])
    pos = sb
keep.append(pcm[pos * width:])
out = b''.join(keep)
with wave.open(str(OUT / '中文解说.wav'), 'wb') as w:
    w.setparams(params)
    w.writeframes(out)
new_dur = len(out) / width / sr

# 3) 时间轴 + 分镜文本
dropped = {t for _, _, _, t in cuts}
for i, sc in enumerate(tl['scenes']):
    sc['captions'] = [dict(c, start=shift(c['start']), end=shift(c['end']))
                      for c in sc['captions'] if c['text'] not in dropped]
    sc['start'], sc['end'] = shift(sc['start']), shift(sc['end'])
    for t in dropped:
        sc['text'] = sc['text'].replace(t, '')
    story[i]['text'] = sc['text']
tl['captions'] = [c for sc in tl['scenes'] for c in sc['captions']]
tl['duration'] = tl['scenes'][-1]['end']
assert abs(tl['duration'] - new_dur) < 0.05, (tl['duration'], new_dur)
(DATA / 'timeline.json').write_text(json.dumps(tl, ensure_ascii=False, indent=2))
(DATA / 'story.json').write_text(json.dumps(story, ensure_ascii=False, indent=2))

for a, b, i, t in cuts:
    print(f'场景{i}  剪 {a:8.2f}–{b:8.2f}  ({b - a:5.2f}s)  {t[:24]}')
print(f'合计剪掉 {total_cut:.2f}s；音轨 {new_dur:.2f}s；时间轴 {tl["duration"]:.2f}s')
