#!/usr/bin/env python3
"""从 timeline.json 导出文字交付件：字幕、章节、解说文稿。

字幕按每行 36 字折行，句尾不带句号（与画面字幕一致）。

用法：
  python src/export_text.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, OUT = ROOT / 'data', ROOT / 'output'


def stamp(sec):
    """秒 → SRT 时间码"""
    ms = round(sec * 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'


def wrap(value, limit):
    return [value[i:i + limit] for i in range(0, len(value), limit)]


def exports(timeline, story):
    srt = []
    for i, c in enumerate(timeline['captions']):
        text = c['text'].rstrip('。．.')
        srt.append(f"{i + 1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n"
                   + '\n'.join(wrap(text, 36)) + '\n')
    (OUT / '中文字幕.srt').write_text('\n'.join(srt))

    meta = [';FFMETADATA1', 'title=中国五千年：山河流转，文明长卷',
            'comment=历史范围与联系方向示意；不作为标准地图或疆界考证依据。']
    for s, t in zip(story, timeline['scenes']):
        meta += ['[CHAPTER]', 'TIMEBASE=1/1000', f"START={round(t['start'] * 1000)}",
                 f"END={round(t['end'] * 1000)}", 'title=' + s['era'] + '｜' + s['title']]
    (OUT / '章节.ffmeta').write_text('\n'.join(meta) + '\n')

    (OUT / '解说文稿.txt').write_text('\n\n'.join(
        f"{i + 1:02} {s['era']}｜{s['title']}\n{s['year']}\n{s['text']}"
        for i, s in enumerate(story)))


def main():
    tl = json.loads((DATA / 'timeline.json').read_text())
    story = json.loads((DATA / 'story.json').read_text())
    assert len(story) == len(tl['scenes']), 'story.json 与 timeline.json 场景数不一致'
    assert all(s['text'] == t['text'] for s, t in zip(story, tl['scenes'])), \
        'story.json 的旁白与 timeline.json 不一致，需先同步'
    OUT.mkdir(exist_ok=True)
    exports(tl, story)
    print(f'· 写出 中文字幕.srt（{len(tl["captions"])} 条）/ 章节.ffmeta（{len(story)} 章）/ 解说文稿.txt')


if __name__ == '__main__':
    main()
