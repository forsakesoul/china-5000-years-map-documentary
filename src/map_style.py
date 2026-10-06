#!/usr/bin/env python3
"""古地图风格渲染原型（任务 2 + 任务 3）。

羊皮纸底 + 水墨地形 + 书法标注，对齐参考图 1.png 的观感。
配色取自参考图实际采样：
  纸底 #f3eddf / #e2dac7   海面 #b9c0b4（灰绿，青瓷感）
  疆域赭金 #dac192          对峙暗赭红 #aa8876
  墨色 #4a382b              朱红 #c74429

数据来源（任务 3）：
  · 高程：Terrarium z5 瓦片（48 块）→ 晕渲 + 山脊线 → 水墨笔触
  · 海岸：Natural Earth 50m land（land50.geojson，比原 land.geojson 细）
  · 河流：rivers.geojson
  · 疆域：world_<year>.geojson 历史快照（本机已有 21 个年份，可换用更细快照）

不修改 generate.py，独立输出 PNG 供确认风格。
用法：
  python src/map_style.py --scene 15 --out work/样式示例-唐.png
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]     # 仓库根目录
SRC = Path(__file__).resolve().parent
ASSETS, DATA = ROOT / 'assets', ROOT / 'data'
WORK = ROOT / 'work'                            # 渲染中间产物（不入库）
STORY = json.loads((DATA / 'story.json').read_text())
GEO = {f['name']: f for f in json.loads((DATA / 'geography.json').read_text())['features']}

sys.path.insert(0, str(SRC))
from map_scenes import SCENES, CITIES, CITY_OFF  # noqa: E402  场景图层配置

W, H = 1920, 1080

# ---------------------------------------------------------------- 投影
LON_C = 103.0
LAT_TOP, LAT_BOT = 56.0, 18.0
STD_PARALLEL = 37.0            # 标准纬线，保证 37°N 附近形状不失真
PPD_LAT = H / (LAT_TOP - LAT_BOT)
PPD_LON = PPD_LAT * math.cos(math.radians(STD_PARALLEL))


def proj(lon, lat):
    return (W / 2 + (lon - LON_C) * PPD_LON, (LAT_TOP - lat) * PPD_LAT)


def unproj(x, y):
    return (LON_C + (x - W / 2) / PPD_LON, LAT_TOP - y / PPD_LAT)


# ---------------------------------------------------------------- 配色
C_PAPER = (243, 237, 223)
C_PAPER_D = (226, 218, 199)
C_SEA = (185, 192, 180)
C_SEA_D = (166, 175, 164)
C_GOLD = (220, 190, 133)
C_GOLD_E = (162, 126, 56)
C_ACCENT = (167, 138, 127)
C_ACCENT_E = (118, 82, 66)
C_NEUTRAL = (208, 205, 190)
C_NEUTRAL_E = (146, 143, 128)
C_INK = (74, 56, 43)
C_INK_DEEP = (52, 40, 32)
C_INK_SOFT = (110, 98, 84)
C_RED = (199, 68, 41)
C_WATER_TXT = (104, 128, 130)

# 多政权并立时的区分色板。
# 全部为低饱和土色系，明度、彩度都压在羊皮纸调子里，避免出现跳色；
# 每个政权按自身特点分配（中原正统用赭金、高原/对立面用赭红、游牧用青灰、
# 海疆/岛国用蓝灰、绿洲与农牧交错用橄榄），不做随机取色。
# 每项为 (填充色, 边界色)。
PALETTE = [
    ((220, 190, 132), (160, 124, 54)),     # 0 赭金   中原王朝、居中的统一政权
    ((176, 126, 106), (114, 74, 58)),      # 1 赭红   与中原对峙的政权（吐蕃、西夏、秦）
    ((146, 166, 138), (86, 106, 80)),      # 2 青灰绿 北方草原、游牧政权
    ((176, 138, 152), (110, 82, 92)),      # 3 灰紫   高原、西南政权
    ((208, 178, 112), (146, 116, 54)),     # 4 土黄   并列的中原式政权、黄土区
    ((140, 162, 178), (84, 102, 120)),     # 5 蓝灰   海疆、岛国、江东水乡
    ((194, 160, 122), (126, 98, 64)),      # 6 浅褐   绿洲、西域
    ((158, 166, 108), (100, 108, 60)),     # 7 橄榄   农牧交错、稻作与盆地
]
LABEL_INK = [(102, 76, 28), (92, 56, 42), (66, 82, 60), (88, 60, 70),
             (96, 72, 28), (58, 72, 88), (86, 64, 40), (72, 78, 44)]

CALLI = str(ASSETS / 'MaShanZheng-Regular.ttf')
SERIF = os.environ.get('MAP_SERIF', '/System/Library/Fonts/Supplemental/Songti.ttc')

_fc: dict = {}


def font(path, size):
    k = (path, size)
    if k not in _fc:
        _fc[k] = ImageFont.truetype(path, size)
    return _fc[k]


_cov: dict = {}


def coverage(path):
    """马善政字体只有 7015 字（「靺鞨」等生僻字缺失），需按字符回退到宋体。"""
    if path not in _cov:
        try:
            from fontTools.ttLib import TTFont
            f = TTFont(path, fontNumber=0) if path.endswith('.ttc') else TTFont(path)
            cm = set()
            for t in f['cmap'].tables:
                cm |= set(t.cmap.keys())
            _cov[path] = cm
        except Exception:
            _cov[path] = None
    return _cov[path]


def runs(s, path):
    """把字符串切成 (是否被字体覆盖, 文本) 的连续段。"""
    cm = coverage(path)
    if cm is None:
        return [(True, s)]
    out, buf, cur = [], '', None
    for ch in s:
        ok = ord(ch) in cm
        if cur is None or ok == cur:
            buf += ch
            cur = ok
        else:
            out.append((cur, buf))
            buf, cur = ch, ok
    if buf:
        out.append((cur, buf))
    return out


# 底图风格（定稿）：A 古籍淡彩的纸底与疆域色带 + B 的晕渲高原 + B 的深浅海面
STYLE = dict(
    paper=(240, 232, 214), paper_grain=1.0,
    sea=(196, 210, 214), sea_deep=(150, 172, 184), shoal=(222, 231, 232),
    relief=0.95, ink=0.10, plateau=(212, 196, 166), plateau_a=0.38,
    wash=0.26, band=0.62, band_w=16.0, line=0.95, river=(112, 146, 150, 120))


# ---------------------------------------------------------------- 工具
def fbm(h, w, octaves=6, base=3, seed=7, aspect=1.6):
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        g = (rng.random((max(2, int(base * 2 ** o)),
                         max(2, int(base * 2 ** o * aspect)))) * 255).astype(np.uint8)
        out += np.asarray(Image.fromarray(g).resize((w, h), Image.BICUBIC), np.float32) / 255 * amp
        tot += amp
        amp *= 0.5
    return out / tot


def gauss(a, sigma):
    from scipy.ndimage import gaussian_filter
    return gaussian_filter(a, sigma, mode='nearest')


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def over(canvas, color, alpha):
    c = np.asarray(color, np.float32)
    a = np.clip(alpha, 0, 1)[..., None]
    return canvas * (1 - a) + c * a


def soften(m, r=3.6, k=0.07):
    """把数据集的稀疏折线磨圆：模糊后再取阈，边界不再是生硬的直线段。

    数据集相邻顶点往往隔几百公里，多边形是「直线拼出来的」；直接填色会看到
    很硬的斜边与尖角（宋辽夏、明这类尤其明显）。模糊后围绕 0.5 重新取一个
    平滑带，面积基本不变，但折角被磨圆。
    """
    b = gauss(m, r)
    return np.clip((b - (0.5 - k)) / (2 * k), 0.0, 1.0)


def mask_from_features(features, ss=2, soft=0.0):
    big = Image.new('L', (W * ss, H * ss), 0)
    d = ImageDraw.Draw(big)
    for feat in features:
        geom = feat['geometry']
        polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
        for poly in polys:
            for i, ring in enumerate(poly):
                pts = [(x * ss, y * ss) for x, y in (proj(v[0], v[1]) for v in ring)]
                if len(pts) >= 3:
                    d.polygon(pts, fill=255 if i == 0 else 0)
    m = np.asarray(big.resize((W, H), Image.LANCZOS), np.float32) / 255.0
    return soften(m, soft) if soft > 0 else m


def outline(mask, gain=1.6, blur=0.9, width=1):
    a = gauss(mask, blur)
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = (a[:, 2:] - a[:, :-2]) * 0.5
    gy[1:-1, :] = (a[2:, :] - a[:-2, :]) * 0.5
    e = np.hypot(gx, gy)
    if width > 1:
        e = np.asarray(Image.fromarray(np.clip(e * 255, 0, 255).astype(np.uint8))
                       .filter(ImageFilter.MaxFilter(int(width) | 1)), np.float32) / 255.0
    return np.clip(e * gain, 0.0, 1.0)


# ---------------------------------------------------------------- 地形
TZ, TX0, TX1, TY0, TY1 = 5, 21, 28, 9, 14


def terrain_grid():
    """拼接 Terrarium 高程瓦片并按画布采样，返回 (elev, 米/像素x, 米/像素y)。"""
    tw, th = (TX1 - TX0 + 1) * 256, (TY1 - TY0 + 1) * 256
    elev = np.zeros((th, tw), np.float32)
    for ty in range(TY0, TY1 + 1):
        for tx in range(TX0, TX1 + 1):
            p = ASSETS / 'terrain' / f'{TZ}-{tx}-{ty}.png'
            if p.exists():
                a = np.asarray(Image.open(p).convert('RGB'), np.float32)
                elev[(ty - TY0) * 256:(ty - TY0 + 1) * 256,
                     (tx - TX0) * 256:(tx - TX0 + 1) * 256] = (
                    a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0)

    lon = LON_C + (np.arange(W, dtype=np.float32) - W / 2) / PPD_LON
    lat = LAT_TOP - np.arange(H, dtype=np.float32) / PPD_LAT
    LON, LAT = np.meshgrid(lon, lat)

    n = 2 ** TZ
    gx = (LON + 180.0) / 360.0 * n * 256.0 - TX0 * 256.0
    gy = (1.0 - np.log(np.tan(np.radians(LAT)) + 1.0 / np.cos(np.radians(LAT)))
          / math.pi) / 2.0 * n * 256.0 - TY0 * 256.0
    x0 = np.floor(gx).astype(np.int32)
    y0 = np.floor(gy).astype(np.int32)
    fx = (gx - x0)[..., None]
    fy = (gy - y0)[..., None]

    def pick(xx, yy):
        ok = (xx >= 0) & (xx < tw) & (yy >= 0) & (yy < th)
        return np.where(ok, elev[np.clip(yy, 0, th - 1), np.clip(xx, 0, tw - 1)], -200.0)

    v = (pick(x0, y0) * (1 - fx[..., 0]) * (1 - fy[..., 0])
         + pick(x0 + 1, y0) * fx[..., 0] * (1 - fy[..., 0])
         + pick(x0, y0 + 1) * (1 - fx[..., 0]) * fy[..., 0]
         + pick(x0 + 1, y0 + 1) * fx[..., 0] * fy[..., 0])
    v = np.maximum(v, 0.0)
    mx = 111320.0 * np.cos(np.radians(LAT)) / PPD_LON
    my = 110540.0 / PPD_LAT
    return v, mx, my


def terrain_ink(elev, mx, my):
    """水墨地形：局部晕渲（去直流取背光面）+ 坡度定量，再叠手绘颗粒。

    为什么不用全局晕渲阈值：Terrarium z5 约 4km/像素，全局 hillshade 只在
    0.58–0.72 之间浮动，直接阈值会切出大块黑斑。改为先减去低频分量得到
    「局部光照偏差」，再按标准差归一，取背光面成墨——得到的是细笔触而非色块。
    """
    dx = np.zeros_like(elev)
    dy = np.zeros_like(elev)
    dx[:, 1:-1] = (elev[:, 2:] - elev[:, :-2]) * 0.5 / mx[:, 1:-1]
    dy[1:-1, :] = (elev[2:, :] - elev[:-2, :]) * 0.5 / my
    tan_s = np.hypot(dx, dy)

    slope = np.arctan(tan_s)
    aspect = np.arctan2(-dx, dy)
    az, alt = math.radians(315.0), math.radians(42.0)
    hs = math.sin(alt) * np.cos(slope) + math.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    shade = np.clip((math.sin(alt) - hs) / math.sin(alt), 0, 1)

    dev = hs - gauss(hs, 10.0)
    sd = float(np.std(dev[elev > 300.0])) + 1e-6
    shadow = np.clip(-dev / (2.0 * sd), 0, 1) ** 1.25        # 背光细笔触
    steep = smoothstep(0.28, 0.95, np.clip(tan_s / 0.090, 0, 1))   # 陡坡加密

    ink = 0.70 * shadow + 0.55 * steep
    ink *= smoothstep(55.0, 430.0, elev)                     # 平原不出墨
    ink *= (0.62 + 0.55 * smoothstep(400.0, 2800.0, elev))
    ink *= (0.58 + 0.72 * fbm(H, W, 6, base=17, seed=41, aspect=1.0))   # 断笔颗粒
    ink *= (0.70 + 0.50 * fbm(H, W, 5, base=3, seed=11))                # 大尺度浓淡
    return np.clip(ink, 0.0, 1.0), shade


# ---------------------------------------------------------------- 文本与场景装饰
def txt(d, xy, s, size, fill, path=SERIF, anchor=None, spacing=0, stroke=0, halo=(250, 246, 236)):
    """支持「书法体缺字自动回退宋体」与字间距的文本绘制。"""
    segs = runs(s, path)
    if len(segs) == 1 and not spacing:
        d.text(xy, s, font=font(path, size), fill=fill, anchor=anchor,
               stroke_width=stroke, stroke_fill=halo)
        return

    def f_of(covered):
        return font(path if covered else SERIF, size)

    widths = [sum(d.textlength(c, font=f_of(cv)) for c in t) for cv, t in segs]
    total = sum(widths) + spacing * max(0, len(s) - 1)
    x, y = xy
    ax = anchor or 'l'
    h, v = ax[0], (ax[1] if len(ax) > 1 else 'a')
    if h == 'm':
        x -= total / 2
    elif h == 'r':
        x -= total
    if v == 'm':
        y -= size * 0.50
    elif v in 'sb':
        y -= size * 0.72
    for (cv, t), w in zip(segs, widths):
        f = f_of(cv)
        for c in t:
            d.text((x, y), c, font=f, fill=fill, stroke_width=stroke, stroke_fill=halo)
            x += d.textlength(c, font=f) + spacing
        x += 0  # 段间不加额外间距


# ---------------------------------------------------------------- 装饰件
def compass(im, cx, cy, r=52):
    d = ImageDraw.Draw(im)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=C_INK, width=2)
    d.ellipse((cx - r + 7, cy - r + 7, cx + r - 7, cy + r - 7), outline=C_INK, width=1)
    for i in range(72):
        a = math.radians(i * 5)
        L = 11 if i % 9 == 0 else (6 if i % 3 == 0 else 3)
        d.line((cx + math.sin(a) * (r - 3), cy - math.cos(a) * (r - 3),
                cx + math.sin(a) * (r - 3 - L), cy - math.cos(a) * (r - 3 - L)),
               fill=C_INK, width=1)
    for ang, col, ln, half in ((0, C_RED, r - 14, 10), (180, (128, 122, 110), r - 18, 9),
                               (90, (128, 122, 110), r - 24, 6), (270, (128, 122, 110), r - 24, 6)):
        a = math.radians(ang)
        d.polygon([(cx + math.sin(a) * ln, cy - math.cos(a) * ln),
                   (cx + math.cos(a) * half, cy + math.sin(a) * half),
                   (cx - math.cos(a) * half, cy - math.sin(a) * half)], fill=col)
    d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(248, 244, 234), outline=C_INK, width=1)
    txt(d, (cx, cy - r - 30), '北', 30, C_RED, CALLI, anchor='mm')
    txt(d, (cx + r + 26, cy), '东', 26, C_INK, CALLI, anchor='mm')
    txt(d, (cx - r - 26, cy), '西', 26, C_INK, CALLI, anchor='mm')
    txt(d, (cx, cy + r + 28), '南', 26, C_INK, CALLI, anchor='mm')


def wrap_cn(s, limit):
    """按标点优先折行，避免把词从中间切断。"""
    lines, cur = [], ''
    for ch in s:
        cur += ch
        if len(cur) >= limit:
            cut = len(cur)
            for back in range(1, 6):
                if len(cur) - back >= 2 and cur[len(cur) - back] in '，。；、！？·':
                    cut = len(cur) - back + 1
                    break
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        lines.append(cur)
    return lines


def cn_num(n):
    d = '一二三四五六七八九'
    if n < 10:
        return d[n - 1]
    if n == 10:
        return '十'
    if n < 20:
        return '十' + d[n - 11]
    return d[n // 10 - 1] + '十' + (d[n % 10 - 1] if n % 10 else '')


def chapter_block(im, x, y, index, title, sub, bullets):
    """左下章节块：卷N + 书法标题 + 年代 + 要点（自动折行，不越界）。"""
    d = ImageDraw.Draw(im)
    txt(d, (x, y), f'卷{cn_num(index + 1)}', 22, (150, 100, 76), CALLI)
    d.line((x, y + 32, x + 72, y + 32), fill=(168, 124, 98), width=1)

    if len(title) <= 6:
        lines, size = [title], 52
    else:
        half = math.ceil(len(title) / 2)
        lines, size = [title[:half], title[half:]], 50
    yy = y + 44
    for ln in lines:
        txt(d, (x, yy), ln, size, (44, 34, 28), CALLI, stroke=1)
        yy += size + 4

    yy += 2
    txt(d, (x, yy), sub, 22, (98, 88, 76))
    yy += 32
    for b in bullets:
        for i, ln in enumerate(wrap_cn(b, 13)):
            txt(d, (x, yy), ('· ' if i == 0 else '   ') + ln, 19, (122, 114, 102))
            yy += 26
        yy += 4
    return yy


def frame(im):
    d = ImageDraw.Draw(im)
    d.rectangle((13, 13, W - 14, H - 14), outline=(104, 90, 74), width=1)
    d.rectangle((20, 20, W - 21, H - 21), outline=(154, 140, 120), width=1)


# ---------------------------------------------------------------- 场景构建
KM_PER_PX = 3.92          # 标准纬线附近，东西向约 111.32*cos37°/PPD_LON
KM_X = 111.32 * math.cos(math.radians(STD_PARALLEL)) / PPD_LON
KM_Y = 110.54 / PPD_LAT


def fill_edge(idx):
    """色板索引 → (填充色, 边界色)；-1 表示「其它并立政权」的统一灰调。"""
    if idx < 0:
        return C_NEUTRAL, C_NEUTRAL_E
    return PALETTE[idx % len(PALETTE)]


def centroid_lonlat(mask):
    """区块的像素质心（经纬度）：把政权名自动放到自己范围的重心。"""
    ys, xs = np.nonzero(mask > 0.5)
    if not len(ys):
        return None
    return unproj(float(xs.mean()), float(ys.mean()))


_WARP = None


def warp(m, amp=6.0):
    """给疆域边界加一层低频扰动：数据集多边形是几百公里一段的直线，
    扰动后像手绘边界。所有图层共用同一张位移场，相邻政权的交界仍严丝合缝。"""
    global _WARP
    from scipy.ndimage import map_coordinates
    if _WARP is None:
        def field(seed):
            n = fbm(H, W, 4, base=7, seed=seed, aspect=1.6)
            return (n - n.mean()) / (n.std() + 1e-6)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        _WARP = (yy + field(61) * amp, xx + field(67) * amp)
    return map_coordinates(m, _WARP, order=1, mode='nearest').astype(np.float32)


def chaikin(pts, n=3):
    """闭合折线圆角化：手绘示意形状只需给少量控制点。"""
    for _ in range(n):
        out = []
        for i in range(len(pts)):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
            out += [(0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1),
                    (0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1)]
        pts = out
    return pts


def shape_feature(pts, smooth=True):
    ring = chaikin(pts) if smooth else list(pts)
    return {'geometry': {'type': 'Polygon', 'coordinates': [[list(p) for p in ring]]}}


def voronoi_regions(mask, seeds):
    """按都城做最近邻分区，只为让并立政权「各自的范围」可读。

    这是一处**明确的示意手段**：数据集把「诸侯国」合并成一个面，
    拿不到真实国界。因此内部边界只画成柔和的虚线感线，并在图上标注示意，
    绝不呈现为精确疆界。
    """
    xs = np.arange(W, dtype=np.float32)[None, :]
    ys = np.arange(H, dtype=np.float32)[:, None]
    dists = []
    for _, lon, lat, _ in seeds:
        sx, sy = proj(lon, lat)
        dx = (xs - sx) * KM_X
        dy = (ys - sy) * KM_Y
        dists.append(dx * dx + dy * dy)
    lab = np.argmin(np.stack([warp(d) for d in dists], 0), 0).astype(np.int16)
    regs = [((lab == i).astype(np.float32) * mask) for i in range(len(seeds))]
    edge = ((lab != np.roll(lab, 1, 0)) | (lab != np.roll(lab, 1, 1))).astype(np.float32)
    return regs, edge * np.clip(mask * 1.6, 0, 1)

FLOW_COL = (156, 106, 58)
SITE_COL = (176, 96, 62)
CAPTION_COL = (46, 38, 30)


def measure(d, s, size, path, spacing=0.0):
    """按段计算文本宽度（含书法体缺字回退），用于给标注加衬底。"""
    w = 0.0
    for cv, t in runs(s, path):
        f = font(path if cv else SERIF, size)
        w += sum(d.textlength(c, font=f) for c in t)
    return w + spacing * max(0, len(s) - 1)


def plate(d, xy, s, size, fill, path=CALLI, spacing=0.0, pad=(18, 9)):
    """纸色衬底 + 文字：示意区名称会互相交叠，垫一层纸底才读得清。"""
    w = measure(d, s, size, path, spacing)
    x, y = xy
    d.rounded_rectangle((x - w / 2 - pad[0], y - size * 0.62 - pad[1],
                         x + w / 2 + pad[0], y + size * 0.44 + pad[1]),
                        radius=7, fill=(247, 243, 232), outline=(198, 182, 156), width=1)
    txt(d, (x, y), s, size, fill, path, anchor='mm', spacing=spacing)


def soft_zone(lon, lat, wkm, hkm):
    """近似示意区：羽化椭圆。只表达大致的空间范围，不构成任何边界。"""
    x, y = proj(lon, lat)
    rx = max(8.0, wkm / 2.0 / KM_PER_PX)
    ry = max(8.0, hkm / 2.0 / KM_PER_PX)
    big = Image.new('L', (W, H), 0)
    ImageDraw.Draw(big).ellipse((x - rx, y - ry, x + rx, y + ry), fill=255)
    a = np.asarray(big, np.float32) / 255.0
    a = gauss(a, max(7.0, min(rx, ry) * 0.26))
    peak = float(a.max())
    return a / peak if peak > 0 else a


def smooth_path(pts, n=14):
    """Catmull-Rom 平滑，把折点连成弧线（商路 / 航线 / 迁徙方向）。"""
    if len(pts) < 3:
        return list(pts)
    P = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(len(P) - 3):
        p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
        for j in range(n):
            t = j / n
            t2, t3 = t * t, t * t * t
            out.append((
                0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)))
    out.append(pts[-1])
    return out


def dashed_line(d, pts, fill, width=2, dash=9, gap=7, phase=0.0):
    """按弧长画虚线，phase 让虚线沿线流动。

    注意：phase 会随动画时间无限增长、acc 也会沿长折线累加，
    两者都很大时 (acc+t+phase) % cycle 可能落到离 dash/cycle 极近的位置，
    使 min(...) 算出的步长小于 t 的浮点分辨率 → t 不再前进 → 死循环。
    因此先对 phase 取模，并强制一个最小步长。"""
    cycle = float(dash + gap)
    phase %= cycle
    acc = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg <= 0:
            continue
        ux, uy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
        t = 0.0
        while t < seg:
            m = (acc + t + phase) % cycle
            if m < dash:
                run = min(dash - m, seg - t)
                d.line((a[0] + ux * t, a[1] + uy * t,
                        a[0] + ux * (t + run), a[1] + uy * (t + run)),
                       fill=fill, width=width)
                step = run
            else:
                step = min(cycle - m, seg - t)
            t += step if step > 1e-6 else 1e-6
        acc += seg


def dashed_ellipse(d, cx, cy, rx, ry, fill, width=2, dash=11, gap=8, phase=0.0):
    """虚线椭圆：明确标出「这是示意范围，不是边界」。"""
    pts = [(cx + rx * math.cos(t), cy + ry * math.sin(t))
           for t in np.linspace(0, 2 * math.pi, 240)]
    dashed_line(d, pts, fill, width=width, dash=dash, gap=gap, phase=phase)


def legend_card(im, x, y):
    d = ImageDraw.Draw(im)
    w, h = 392, 176
    d.rounded_rectangle((x, y, x + w, y + h), radius=6,
                        fill=(246, 241, 229), outline=(176, 160, 134), width=1)
    d.line((x + 14, y + 34, x + w - 14, y + 34), fill=(206, 192, 168), width=1)
    txt(d, (x + 16, y + 12), '读 图 方 法', 22, (128, 100, 70), CALLI)
    ty = y + 58
    d.rectangle((x + 18, ty, x + 56, ty + 22), fill=C_GOLD, outline=C_GOLD_E, width=1)
    txt(d, (x + 70, ty + 11), '色块：历史范围的近似表达', 19, (104, 96, 84), SERIF, anchor='lm')
    ty += 38
    d.ellipse((x + 26, ty + 1, x + 48, ty + 23), outline=SITE_COL, width=2)
    d.ellipse((x + 34, ty + 9, x + 40, ty + 15), fill=SITE_COL)
    txt(d, (x + 70, ty + 11), '光点：文化与城市的位置', 19, (104, 96, 84), SERIF, anchor='lm')
    ty += 38
    dashed_line(d, [(x + 18, ty + 12), (x + 41, ty + 4), (x + 58, ty + 16)],
                FLOW_COL, width=3)
    txt(d, (x + 70, ty + 11), '流线：迁徙和交流的方向', 19, (104, 96, 84), SERIF, anchor='lm')


def credits_page(im):
    """片尾说明：不用圆章与章节块，改成整幅说明版式。"""
    d = ImageDraw.Draw(im)
    scene = STORY[33]
    txt(d, (W / 2, 322), scene['title'], 62, (58, 46, 36), CALLI, anchor='mm')
    d.line((W / 2 - 150, 372, W / 2 + 150, 372), fill=(176, 152, 116), width=1)
    yy = 430
    for b in scene['bullets']:
        txt(d, (W / 2, yy), b, 30, (110, 100, 86), SERIF, anchor='mm')
        yy += 56
    yy += 34
    txt(d, (W / 2, yy), scene['text'], 24, (132, 122, 108), SERIF, anchor='mm')
    txt(d, (W / 2, H - 210), '底图 Historical Basemaps / Natural Earth / Terrarium',
        22, (140, 130, 116), SERIF, anchor='mm')
    txt(d, (W / 2, H - 174), '历史范围与联系方向均为示意，不作为疆界考证依据',
        22, (140, 130, 116), SERIF, anchor='mm')
    txt(d, (W / 2, H - 120), '地图动画 · 中文解说 · 历史概览', 26, (150, 118, 82), CALLI, anchor='mm')


def seal(im, cx, cy, r, era, big, sub, big_size=None):
    """朱红纪年圆章；big 为年份或朝代号，字号按长度自适应。"""
    d = ImageDraw.Draw(im)
    rng = np.random.default_rng(5)
    for k in range(3):
        pts = [(cx + math.cos(math.radians(i)) * (r + rng.normal(0, 1.7)),
                cy + math.sin(math.radians(i)) * (r + rng.normal(0, 1.7)))
               for i in range(0, 361, 3)]
        d.line(pts, fill=(66, 54, 44), width=2 if k == 0 else 1, joint='curve')
    es = 34                                  # 顶行朝代名同样不超出圆（圆在此处较窄）
    while d.textlength(era, font=font(CALLI, es)) > r * 1.25 and es > 20:
        es -= 2
    txt(d, (cx, cy - r * 0.46), era, es, C_INK, CALLI, anchor='mm')
    is_year = (big[2:] if big[:2] in ('BC', 'AD') else big).isdigit()
    # 中间一行（年份 +「年」）总宽不超过圆内可用宽度，超了就缩字号
    size = big_size or min(64, int((r * 1.55) / max(1, len(big))))
    while True:
        f = font(CALLI, size)
        tw = d.textlength(big, font=f)
        ys = max(20, int(size * 0.5))
        yw = d.textlength('年', font=font(CALLI, ys)) + 6 if is_year else 0
        if tw + yw <= r * 1.55 or size <= 22:
            break
        size -= 2
    nx = cx - (tw + yw) / 2                 # 年份与「年」作为整体居中
    base = cy + r * 0.06 + size * 0.36      # 两者共用这条底线，不再错行
    txt(d, (nx, base), big, size, C_RED, CALLI, anchor='ls')
    if is_year:
        txt(d, (nx + tw + 6, base), '年', ys, C_RED, CALLI, anchor='ls')
    if sub:                                  # 副标题在圆内下沿，过长按「 · 」拆成两行
        lines = sub.split(' · ') if d.textlength(sub, font=font(SERIF, 16)) > r * 1.3 else [sub]
        for k, ln in enumerate(lines):
            txt(d, (cx, cy + r * (0.50 + 0.22 * k)), ln, 16, (124, 116, 104), SERIF, anchor='mm')


# ---------------------------------------------------------------- 欧亚视角小地图
def _mask_win(features, win, w, h, ss=2):
    """在指定经纬度窗口内生成掩膜，供小地图使用（独立于主投影）。"""
    lon0, lat0, lon1, lat1 = win
    big = Image.new('L', (w * ss, h * ss), 0)
    d = ImageDraw.Draw(big)
    for feat in features:
        geom = feat['geometry']
        polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
        for poly in polys:
            for i, ring in enumerate(poly):
                pts = [((v[0] - lon0) / (lon1 - lon0) * w * ss,
                        (lat1 - v[1]) / (lat1 - lat0) * h * ss) for v in ring]
                if len(pts) >= 3:
                    d.polygon(pts, fill=255 if i == 0 else 0)
    return np.asarray(big.resize((w, h), Image.LANCZOS), np.float32) / 255.0


def inset_panel(im, spec):
    """欧亚视角小地图：说明「元朝只是蒙古诸汗国之一，金帐汗国最远达东欧」。

    主图是东亚局部视窗，容不下从东欧到日本海的范围；这里用一个独立窗口的小图
    把各汗国的相对位置讲清楚，避免把整幅图拉伸成变形的大陆。
    """
    x, y, w, h = spec['box']
    win = spec['win']
    features = json.loads((ASSETS / f"world_{spec['src']}.geojson").read_text())['features']
    land_w = _mask_win(json.loads((ASSETS / 'land50.geojson').read_text())['features'],
                       win, w, h)

    arr = np.zeros((h, w, 3), np.float32)
    arr[...] = (234, 229, 215)
    arr = over(arr, (192, 201, 193), np.clip(1 - land_w, 0, 1) * 0.80)     # 海面
    for unit, color, _lab in spec['units']:
        m = _mask_win([f for f in features if f['properties'].get('NAME') == unit],
                      win, w, h)
        if float(m.max()) <= 0.5:
            continue
        arr = over(arr, fill_edge(color)[0], np.clip(m * 0.86, 0, 1))
        arr = over(arr, fill_edge(color)[1], np.clip(outline(m, 1.6, 0.9) * 0.9, 0, 1))
    panel = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert('RGBA')
    pd = ImageDraw.Draw(panel)
    pd.rectangle((0, 0, w - 1, h - 1), outline=(176, 160, 132), width=1)
    pd.rectangle((3, 3, w - 4, h - 4), outline=(214, 202, 178), width=1)

    # 各汗国名称放在自身范围中心（窗口坐标）
    for unit, color, lab in spec['units']:
        m = _mask_win([f for f in features if f['properties'].get('NAME') == unit],
                      win, w, h)
        if float(m.max()) <= 0.5:
            continue
        ys_, xs_ = np.nonzero(m > 0.5)
        cx_, cy_ = float(xs_.mean()), float(ys_.mean())
        col = LABEL_INK[color % len(LABEL_INK)] if color >= 0 else (110, 102, 88)
        txt(pd, (cx_, cy_), lab, spec.get('label_size', 17),
            col, CALLI, anchor='mm', spacing=2, stroke=2)

    # 西征方向：金帐汗国再往西就是东欧
    if spec.get('arrow'):
        pd.line((w - 34, h * 0.30, 12, h * 0.30), fill=(178, 72, 48), width=3)
        pd.polygon([(12, h * 0.30), (26, h * 0.30 - 7), (26, h * 0.30 + 7)],
                   fill=(178, 72, 48))
        txt(pd, (w / 2, h * 0.30 - 15), spec['arrow'], 15, (156, 60, 40),
            CALLI, anchor='mm')

    im.paste(panel, (x, y), panel)
    d = ImageDraw.Draw(im)
    txt(d, (x + 2, y - 26), spec['title'], 21, (92, 72, 46), CALLI)
    if spec.get('note'):
        txt(d, (x + 2, y + h + 6), spec['note'], 15, (128, 118, 104), SERIF)


def render_state(scene, cfg, scene_idx, mode):
    """按一份配置渲染一张静态底图（最重的一遍）：地形、填色、描边、文字、装饰。"""
    yr = cfg.get('src')
    soft = cfg.get('soft', 6.0)      # 数据集的稀疏折线默认磨圆，边界更自然
    inset = cfg.get('inset')

    elev, mx, my = terrain_grid()
    land = mask_from_features(json.loads((ASSETS / 'land50.geojson').read_text())['features'])

    m_pri = np.zeros((H, W), np.float32)
    layers = []          # 每个 layer: dict(mask, color, alpha, edge, label, label_xy, plate)
    voro_edge = None

    if yr:
        data = json.loads((ASSETS / f'world_{yr}.geojson').read_text())

        shapes = cfg.get('shapes') or {}

        def feats(names):
            names = names or []
            out = [f for f in data['features'] if f['properties'].get('NAME') in names]
            # '@名字' 引用场景内手绘示意形状：数据集缺失或明显有误时才用
            return out + [shape_feature(shapes[n[1:]]) for n in names if n.startswith('@')]

        from scipy.ndimage import distance_transform_edt as _edt
        land_b = land > 0.5
        d_sea = _edt(land_b)

        def umask(names, minus=None):
            if not names:
                return np.zeros((H, W), np.float32)
            m = mask_from_features(feats(names))
            if minus:
                m = m * (1 - mask_from_features(feats(minus)))
            m = soften(warp(m), soft) if soft > 0 else warp(m)
            # 贴岸：数据集海岸线比 Natural Earth 粗，岸边会漏出一条无主陆地细条
            mb = m > 0.5
            if mb.any():
                # 只补夹在政权与海之间的窄条（两段距离之和很小），不吃进邻国沿岸
                sliver = land_b & ~mb & (_edt(~mb) + d_sea < 12)
                m = np.maximum(m, np.clip(gauss(sliver.astype(np.float32), 1.5) * 1.6, 0, 1) * land)
            return m

        groups = cfg.get('groups') or []
        voro = cfg.get('voronoi')
        m_neu = umask(cfg.get('neutral'))
        if float(m_neu.max()) > 0.5:
            layers.append(dict(mask=m_neu, color=-1, alpha=0.22, edge=0.42,
                               label=None, label_xy=None, plate=False))
        if groups:
            for g in groups:
                m = umask(g['units'], g.get('minus'))
                layers.append(dict(mask=m, color=g['color'], alpha=g.get('alpha', 0.70),
                                   edge=g.get('edge', 0.95), label=g.get('label'),
                                   label_xy=g.get('label_at'), plate=False,
                                   label_size=g.get('label_size', 44),
                                   diffuse=g.get('diffuse', False)))
                m_pri = np.maximum(m_pri, m)
        else:
            m_pri = umask(cfg.get('primary'))
            m_acc = umask(cfg.get('accent'))
            if float(m_acc.max()) > 0.5:
                layers.append(dict(mask=m_acc, color=1, alpha=0.66, edge=0.95,
                                   label=None, label_xy=None, plate=False))
            if float(m_pri.max()) > 0.5 and not voro:
                layers.append(dict(mask=m_pri, color=0, alpha=0.80, edge=1.0,
                                   label=None, label_xy=None, plate=False))
        if voro:
            vm = umask(voro['units'], voro.get('minus'))
            regs, voro_edge = voronoi_regions(vm, voro['seeds'])
            for (nm, lon, lat, ci), rm in zip(voro['seeds'], regs):
                layers.append(dict(mask=rm, color=ci, alpha=voro.get('alpha', 0.62),
                                   edge=0.70, label=nm, label_xy=(lon, lat), plate=False,
                                   label_size=voro.get('label_size', 44)))
            m_pri = np.maximum(m_pri, vm)

    # --- 示意区（无可靠快照时使用，绝不当作疆界）
    zones = cfg.get('zones') or []
    for z in zones:
        zm = soft_zone(z['lon'], z['lat'], z['w'], z['h'])
        layers.append(dict(mask=zm, color=z.get('color', 0), alpha=z.get('alpha', 0.78),
                           edge=z.get('edge', 0.55), label=z['label'],
                           label_xy=(z['lx'], z['ly']), plate=True))

    from scipy.ndimage import distance_transform_edt as edt, binary_closing
    S = STYLE
    landb = land > 0.5
    # --- 纸底
    n1 = fbm(H, W, 6, base=3, seed=3)
    n2 = fbm(H, W, 7, base=30, seed=9, aspect=1.0)
    g = S['paper_grain']
    paper = np.stack([S['paper'][i] * (1 - 0.05 * g + 0.10 * g * n1) + (n2 - 0.5) * 6 * g
                      for i in range(3)], -1)
    # --- 海：离岸越远越深，近岸一圈浅滩
    dsea = gauss(edt(~landb).astype(np.float32), 1.2)
    deep = smoothstep(0, 140, dsea)[..., None]
    sea = np.asarray(S['sea'], np.float32) * (1 - deep) + np.asarray(S['sea_deep'], np.float32) * deep
    sea = sea * (0.985 + 0.03 * fbm(H, W, 5, base=4, seed=21))[..., None]
    canvas = sea * (1 - land[..., None]) + paper * land[..., None]
    canvas = over(canvas, S['shoal'], np.exp(-dsea / 10.0) * 0.75 * (1 - land))

    # --- 地形：柔和晕渲 + 高原淡赭，墨笔只留极少
    ink, shade = terrain_ink(elev, mx, my)
    soft_shade = gauss(shade, 1.4)
    canvas = over(canvas, (96, 84, 68), np.clip(soft_shade * S['relief'] * land, 0, 0.5))
    lit = np.clip(0.35 - soft_shade, 0, 0.35) * land
    canvas = over(canvas, (255, 252, 244), lit * 0.5)
    plateau = smoothstep(1500.0, 4200.0, elev) * land
    canvas = over(canvas, S['plateau'], plateau * S['plateau_a'])
    if S['ink'] > 0:
        canvas = over(canvas, C_INK, np.clip(gauss(ink, 0.6) * S['ink'] * land, 0, 1))

    # --- 河流
    rim = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rim)
    nrv = 0
    for f in json.loads((ASSETS / 'rivers.geojson').read_text())['features']:
        geom = f['geometry']
        segs = [geom['coordinates']] if geom['type'] == 'LineString' else geom['coordinates']
        for seg in segs:
            pts = [proj(p[0], p[1]) for p in seg]
            if any(-60 < x < W + 60 and -60 < y < H + 60 for x, y in pts):
                rd.line(pts, fill=S['river'], width=2, joint='curve')
                nrv += 1
    canvas = np.asarray(Image.alpha_composite(
        Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8)).convert('RGBA'),
        rim.filter(ImageFilter.GaussianBlur(0.7))).convert('RGB'), np.float32)

    # --- 疆域
    neu = [L for L in layers if L['color'] < 0]
    main = [L for L in layers if L['color'] >= 0]
    for L in neu:      # 周边政权：极淡底 + 细线，只作背景
        m = L['mask']
        canvas = over(canvas, C_NEUTRAL, np.clip(m * 0.16, 0, 1) * land)
        canvas = over(canvas, C_NEUTRAL_E, outline(smoothstep(.42, .58, m), 1.1, 0.8) * 0.28 * land)
    ms = [np.clip(L['mask'], 0, 1) for L in main]
    if len(ms) > 1:    # 相邻政权间的数据空缝按就近归属补齐
        raw = np.clip(sum(ms), 0, 1) > 0.5
        gap = binary_closing(raw, np.ones((15, 15), bool)) & ~raw & landb
        if gap.any():
            bl = [gauss(m, 6) for m in ms]
            tot = sum(bl) + 1e-6
            ms = [np.where(gap, np.maximum(m, b / tot), m) for m, b in zip(ms, bl)]
    for L, m in zip(main, ms):
        L['mask'] = m
        fill, ecol = fill_edge(L['color'])
        if L.get('diffuse'):
            # 保留形状、只把外缘晕开；模糊太大会糊成一个椭圆，也会和邻国之间留出空带
            dm = smoothstep(0.15, 0.85, gauss(m, 10))
            canvas = over(canvas, fill, np.clip(dm * 0.46 * L['alpha'] / 0.7, 0, 1) * land)
            continue
        inside = m > 0.5
        other = landb & ~inside                  # 海岸不算国界：只对「别的陆地」起色带
        d = gauss(edt(~other).astype(np.float32), 1.0)
        band = np.exp(-d / S['band_w']) * m * land
        canvas = over(canvas, fill, np.clip(m * S['wash'] * L['alpha'] / 0.7, 0, 1) * land)
        canvas = over(canvas, fill, np.clip(band * S['band'], 0, 1))
        near = np.clip(gauss(other.astype(np.float32), 2.0) * 5, 0, 1)
        crisp = smoothstep(0.42, 0.58, m)
        canvas = over(canvas, ecol, np.clip(outline(crisp, 1.9, 0.7) * S['line'] * near * land, 0, 1))
    if voro_edge is not None:
        canvas = over(canvas, (108, 90, 66), np.clip(gauss(voro_edge, 1.1) * 0.6, 0, 1))

    # --- 海岸线：细、略淡
    coast = outline(land, gain=1.2, blur=0.8)
    canvas = over(canvas, (92, 104, 100), np.clip(coast * 0.55, 0, 1))
    im = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)

    # --- 区块名称
    for L in layers:
        if not L['label']:
            continue
        xy = L['label_xy']
        if xy is None:
            xy = centroid_lonlat(L['mask'])
        if xy is None:
            continue
        x, y = proj(*xy)
        col = (110, 102, 88) if L['color'] < 0 else LABEL_INK[L['color'] % len(LABEL_INK)]
        if L['plate']:
            plate(d, (x, y), L['label'], 31, col)
        else:
            txt(d, (x, y), L['label'], L.get('label_size', 44), col, CALLI,
                anchor='mm', spacing=8, stroke=3)

    # 示意区画虚线范围框：一眼能看出这是「示意范围」而不是国界
    for z in zones:
        cx, cy = proj(z['lon'], z['lat'])
        rx = max(8.0, z['w'] / 2.0 / KM_PER_PX)
        ry = max(8.0, z['h'] / 2.0 / KM_PER_PX)
        dashed_ellipse(d, cx, cy, rx, ry, fill_edge(z.get('color', 0))[1], width=2)

    for s, lon, lat, size in cfg.get('sea') or []:
        txt(d, (proj(lon, lat)[0], proj(lon, lat)[1]), s, size, C_WATER_TXT, CALLI,
            anchor='mm', spacing=6)

    for name in cfg.get('geo') or []:
        g = GEO.get(name)
        if not g:
            continue
        x, y = proj(g['lon'], g['lat'])
        txt(d, (x, y), name, max(20, int(g['size'] * 0.72)), (122, 112, 96), SERIF,
            anchor='mm', spacing=8)

    for s, lon, lat, size, kind in cfg.get('calli') or []:
        x, y = proj(lon, lat)
        col = {'primary': (104, 78, 30), 'accent': (96, 62, 48),
               'neutral': (110, 102, 88)}[kind]
        txt(d, (x, y), s, size, col, CALLI, anchor='mm', spacing=12, stroke=3)

    for s, lon, lat, size in cfg.get('small') or []:
        txt(d, (proj(lon, lat)[0], proj(lon, lat)[1]), s, size - 1, (124, 116, 102),
            SERIF, anchor='mm')

    inside_mask = m_pri if float(m_pri.max()) > 0.5 else None
    for s, lon, lat, size in cfg.get('inside') or []:
        x, y = proj(lon, lat)
        if inside_mask is not None:
            xi, yi = int(round(x)), int(round(y))
            if not (0 <= yi < H and 0 <= xi < W) or inside_mask[yi, xi] < 0.5:
                continue
        txt(d, (x, y), s, size, (126, 94, 44), CALLI, anchor='mm', spacing=4, stroke=3)

    for name in cfg.get('cities') or []:
        x, y = proj(*CITIES[name])
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(48, 40, 34),
                  outline=(250, 246, 236), width=1)
        dx, dy = CITY_OFF.get(name, (14, -28))
        txt(d, (x + dx, y + dy), name, 25, (48, 40, 34), stroke=2)

    for name in cfg.get('sites') or []:
        x, y = proj(*CITIES[name])
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=SITE_COL,
                  outline=(250, 246, 236), width=1)
        dx, dy = CITY_OFF.get(name, (16, -30))
        txt(d, (x + dx, y + dy), name, 24, (120, 58, 40), stroke=2)

    # --- 固定装饰层：罗盘 / 圆章 / 章节块 / 图例不参与推镜，字更锐利。
    #     画面边缘原本的两条画框线按老板要求去掉，不再绘制。
    deco = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    if mode != 'credits':
        compass(deco, 116, 130, 52)
        ev = cfg.get('evolve')
        if ev:
            top, big, big_size = scene['era'], f"{ev['src']}–{(yr or '').upper()}", 34
        elif mode == 'choropleth':
            top, big, big_size = scene['era'], str(cfg.get('seal_year') or yr or '').upper(), None
        else:
            top, big, big_size = ('年代' if mode == 'zones' else '时期'), scene['era'], 46
        seal(deco, W - 142, 138, 92, top, big,
             (ev.get('seal_sub', '') if ev else ''), big_size)
        if cfg.get('legend'):
            legend_card(deco, W - 452, 250)
        end = chapter_block(deco, 90, H - 320, scene_idx, scene['title'],
                            scene['year'].split(' · ')[0], scene['bullets'][:2])
        assert end < H - 40, f'章节块越界: {end}'
        if inset:
            inset_panel(deco, inset)
    else:
        credits_page(im)

    # 页脚两行说明文字（历史范围约示 / 历史地理示意 · 中文解说）已按要求移除

    return im, deco, nrv


def build(scene_idx):
    """构建场景静态图层；带 evolve 的场景额外渲染一张「前期」底图，供场景内衍化。"""
    scene, cfg = STORY[scene_idx], SCENES[scene_idx]
    mode = cfg['mode']
    im, deco, nrv = render_state(scene, cfg, scene_idx, mode)
    alt = None
    ev = cfg.get('evolve')
    if ev:
        cfg_early = dict(cfg)
        for k in ('src', 'primary', 'accent', 'neutral', 'groups', 'voronoi', 'soft'):
            if k in ev:
                cfg_early[k] = ev[k]
        cfg_early['evolve'] = None
        cfg_early['inset'] = None
        alt, _, _ = render_state(scene, cfg_early, scene_idx, mode)
    return dict(scene=scene, cfg=cfg, base_map=im, alt_map=alt, base_deco=deco,
                mode=mode, nrv=nrv)


def compose(ctx, prev_map, t, dur, caption, global_t, total, scene_idx):
    """逐帧合成：缓慢推镜 + 转场溶解 + 流线生长 + 光点脉冲 + 字幕 + 进度条。"""
    scene, cfg = ctx['scene'], ctx['cfg']
    p = min(1.0, t / max(1e-6, dur))
    z = 1.0 + 0.032 * p                     # 全程缓慢推近约 3%
    cw, ch = W / z, H / z
    cx = 0.5 + 0.011 * (p - 0.5)            # 轻微横移，避免机械感
    cy = 0.5 - 0.009 * (p - 0.5)
    x0 = min(max(cx * W - cw / 2, 0.0), W - cw)
    y0 = min(max(cy * H - ch / 2, 0.0), H - ch)
    cur = ctx['base_map'].crop((x0, y0, x0 + cw, y0 + ch)).resize((W, H), Image.BILINEAR)
    if ctx.get('alt_map') is not None:
        # 疆域衍化：场景内由「前期」缓变到「后期」（如河西走廊由唐入吐蕃）
        early = ctx['alt_map'].crop((x0, y0, x0 + cw, y0 + ch)).resize((W, H), Image.BILINEAR)
        cur = Image.blend(early, cur, float(smoothstep(0.12, 0.86, p)))
    if prev_map is not None and t < 1.6:
        im = Image.blend(prev_map, cur, min(1.0, t / 1.6))
    else:
        im = cur

    def tp(x, y):                           # 地图坐标 → 当前帧屏幕坐标
        return (x - x0) * z, (y - y0) * z

    if ctx['mode'] == 'credits':
        im.paste(ctx['base_deco'], (0, 0), ctx['base_deco'])
        return im

    d = ImageDraw.Draw(im)

    for rno, flow in enumerate(cfg.get('flows') or []):
        pts = [tp(*proj(*q)) for q in flow]
        pts = smooth_path(pts, 16)
        n = int(len(pts) * min(1.0, max(0.0, t - 1.0 - rno * 0.4) / 6.0))
        if n < 2:
            continue
        # 先压一条纸色底线再叠细虚线，线条在复杂地形上也能看清，不再是一根毛糙的线
        dashed_line(d, pts[:n], (250, 245, 233), width=5, dash=8, gap=6, phase=-t * 24)
        dashed_line(d, pts[:n], FLOW_COL, width=2, dash=8, gap=6, phase=-t * 24)
        if t > 1.4:
            q = pts[int(((t * 0.085 + rno * 0.33) % 1.0) * (n - 1))]
            d.ellipse((q[0] - 8, q[1] - 8, q[0] + 8, q[1] + 8),
                      fill=(250, 246, 236), outline=(206, 166, 96), width=2)
            d.ellipse((q[0] - 4, q[1] - 4, q[0] + 4, q[1] + 4), fill=(190, 132, 52))

    for k, name in enumerate(cfg.get('sites') or []):
        x, y = tp(*proj(*CITIES[name]))
        r = 11 + ((t * 0.55 + k * 0.28) % 1.0) * 15
        d.ellipse((x - r, y - r, x + r, y + r), outline=SITE_COL, width=2)

    im.paste(ctx['base_deco'], (0, 0), ctx['base_deco'])

    cap = (caption or '').rstrip('。．.').strip()      # 字幕结尾不再用句号收尾
    for i, ln in enumerate(wrap_cn(cap, 22) if cap else []):
        txt(d, (W / 2 + 30, H - 92 + i * 36), ln, 27, CAPTION_COL, SERIF,
            anchor='mm', stroke=2)
    return im


def render(scene_idx, out_path, t=8.0):
    """渲染单张静态图（抽样确认用）。"""
    scene = STORY[scene_idx]
    ctx = build(scene_idx)
    print(f'场景 {scene_idx}: {scene["era"]}｜{scene["title"]}  '
          f'mode={ctx["cfg"]["mode"]} src={ctx["cfg"].get("src")} 河流 {ctx["nrv"]} 段')
    total, g = 1.0, 0.0
    tl_path = DATA / 'timeline.json'
    if tl_path.exists():
        tl = json.loads(tl_path.read_text())
        total = tl['duration']
        g = sum(s['end'] - s['start'] for s in tl['scenes'][:scene_idx]) + t
    first = scene['text'].split('。')[0] + '。'
    im = compose(ctx, None, t, 30.0, first, g, total, scene_idx)
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    except Exception:
        pass
    im.save(out_path, quality=95)
    print(f'  写出 {out_path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', type=int, default=15)
    ap.add_argument('--out', default=None)
    ap.add_argument('--t', type=float, default=8.0)
    a = ap.parse_args()
    out = a.out or f'work/样式示例-{a.scene:02}.png'
    render(a.scene, str(ROOT / out) if not out.startswith('/') else out, a.t)
