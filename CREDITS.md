# 来源、许可与引用

本片画面由程序生成，但底图、地形与字体来自第三方公开数据。以下逐条列出，并说明使用方式与限制。
文件级 sha256 校验值见 [assets/sources.json](assets/sources.json)，可用 `node scripts/download_assets.cjs` 核对。

## 数据

### Historical Basemaps — 历史疆域快照

- 来源：<https://github.com/aourednik/historical-basemaps>（作者 André Ourednik 及贡献者）
- 随包文件：`assets/world_<年份>.geojson`，共 17 个年份
  （前 1000、前 500、前 300、前 200、前 100、200、400、600、700、800、1000、1200、1279、1600、1700、1800、1900）
- 许可：GPL-3.0，全文见 `assets/LICENSE-historical-basemaps.txt`，上游说明见 `assets/README-historical-basemaps.md`
- 使用方式与限制：
  - 画面只取快照中**实际存在的政权多边形**，不凭空补面；圆章标注所用年份。
  - 数据集为持续修订中的项目，上游明确建议学术使用前与其他来源比对。
  - 部分年份存在名称与年代错配（例如 `world_700` 的中国多边形被标为 `Sui Empire`），
    本片已知并作出标注，未当作精确史实；`src/map_scenes.py` 的 `validate()` 会在渲染前
    校验引用的政权名是否真实存在，避免静默丢层。
  - 数据集把多国合并为单一多边形的场景（春秋、战国的「Zhou states」），用「就近分区」示意，
    并在画面注明「分区示意」。

### Natural Earth — 陆地与河流矢量

- 来源：<https://github.com/nvkelso/natural-earth-vector>
- 随包文件：
  - `assets/land50.geojson` = `ne_50m_land.geojson`（1:50m 陆地轮廓，1420 个要素）
  - `assets/rivers.geojson` = `ne_50m_rivers_lake_centerlines.geojson`（1:50m 河流与湖泊中心线，462 个要素）
- 许可：public domain（<https://www.naturalearthdata.com/about/terms-of-use/>）
- 使用方式与限制：只使用自然地理图层，**未使用其政治国界数据**。陆地轮廓与河道为现代参考线，
  未复原历史时期的海岸线与河道变迁。

### Terrarium 高程瓦片（AWS Open Data）

- 来源：`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`
- 随包文件：`assets/terrain/5-*.png`，z5 共 48 块
- 用于地形晕渲与高原着色。z5 约 4km/像素，只提供质感，不用于精细地形判断。

## 字体

### Ma Shan Zheng（马善政毛笔楷书）

- 来源：<https://github.com/google/fonts/tree/main/ofl/mashanzheng>（Google Fonts）
- 随包文件：`assets/MaShanZheng-Regular.ttf`，许可全文 `assets/OFL-MaShanZheng.txt`
- 许可：SIL Open Font License 1.1，允许再分发与嵌入
- 说明：随包副本与上游当前修订并非同一文件（上游字体会更新），sha256 以 `assets/sources.json` 为准。
  字体缺字时渲染器按字符回退到系统宋体。

## 依赖库

| 库 | 版本 | 用途 | 许可 |
|---|---|---|---|
| numpy | 2.5.3 | 数组与影像运算 | BSD-3-Clause |
| scipy | 1.18.1 | 高斯模糊、距离变换、连通域 | BSD-3-Clause |
| pillow | 12.3.0 | 绘图、字体、图像合成 | MIT-CMU（HPND） |
| imageio-ffmpeg | 0.6.0 | 随包 ffmpeg 二进制，编码与封装 | BSD-2-Clause |
| requests | 2.34.2 | 云端 TTS 调用（可选） | Apache-2.0 |

ffmpeg 本身按 LGPL/GPL 分发，具体以 imageio-ffmpeg 随包二进制的构建配置为准。

## 参考文献与画法参照

- 谭其骧主编《中国历史地图集》（中国地图出版社）：手绘示意范围的轮廓参照。
- 历史地名、政权年表与疆域沿革的常用工具书与通行结论（如《中国历史大辞典》、
  《辞海》历史地理条目），用于核对都城、边郡与关隘的年代归属。
- 引用性质说明：本片的示意范围**不是**对上述文献的复制或数字化，而是按通行画法绘制的示意图形；
  核对凭通行史实，未逐页比对原图。

## 本片原创部分

解说文稿（`output/解说文稿.txt`）、分镜与场景设计、配色与视觉风格、
以及 `src/` 下的全部脚本为原创内容，按 GPL-3.0 提供，以保持与历史图层数据的许可兼容。
解说由云端 TTS 合成，非真人录音，也未伪称经过学术文献逐条审校。
