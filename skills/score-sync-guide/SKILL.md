---
name: score-sync-guide
description: 在 Windows 上把一部管弦乐作品的 MusicXML 总谱和一份录音，做成"谱面同步滚动 + 导赏解析卡"的长视频（跟谱听）。Use when the user wants a score-following / score-synchronized video or a guided-listening video from MusicXML plus an audio recording; when aligning a score to a recording bar-by-bar or note-by-note; when rendering a scrolling single-line score with headless Edge and ffmpeg on native Windows; or when writing analysis cards and cross-checking them against the score.
---

# 跟谱听（score-sync-guide）

把 **MusicXML 总谱 + 一份录音** 做成一支视频：左边是单行横向滚动的总谱，播放线固定不动，正在发声的音符变红，只显示正在演奏的声部；右边是导赏解析卡和本乐章的曲式结构。全流程在原生 Windows 上跑，不需要 WSL、Node 或 Playwright。

这套做法来自一部马勒第六交响曲（四个乐章、83 分钟）的实际制作。其中**逐起音对齐算法移植自 [tofucurd/score-sync-video](https://github.com/tofucurd/score-sync-video)（MIT）**，播放器的若干设计（固定游标、稳定的声部集合、两级曲式结构、按块可续渲）也借鉴了它；本 skill 是它在 Windows 上的本地化与扩展（导赏卡片、录音对齐流程、交叉核对工具）。

## 先读这几条规矩

1. **原始文件不动。** 谱面、录音放在项目根目录，只读；改谱一律写进 `scores_fixed/corrections.json`，由 `tools/fix_mxl.py` 生成副本，并记录依据（原谱页码、截图）。
2. **改音符先问用户。** 社区 MusicXML 与印刷总谱常是不同版本，"不一致"不一定是错。只修明显的导入错误。
3. **商业录音不上传到任何第三方服务**，也不进仓库。
4. **新装 Python 包由用户自己装**：列出包名和命令，不要擅自 `pip install`。
5. **解析卡里每一条事实性断言都要有依据**：谱面数据（写明小节号）或可核查的文献（`references.json` + 角标）。查不到就不写，或标"未核实"。自动和声分析对大编制管弦乐不可靠，只作线索。
6. 用户不识谱时，验收靠耳朵：**对着谱面听**（争议片段合集）比听"嗒"声更可靠。
7. **先跑逐起音细对齐，再请用户听、再加钉点。** 反过来做（先让用户逐个报时刻），会把大量精力花在细对齐本来就能自动解决的小节内偏移上。
8. 用户的个人看法只作点缀，不替用户编造或夸大观点。

## 流程总览

| 步骤 | 做什么 | 主要文件 | 详见 |
|---|---|---|---|
| 0 | 建项目：把 `template/` 复制到项目根目录，写 `manifest.json` | `template/`、`examples/mahler6/manifest.json` | [workflow](references/workflow.md) |
| 1 | 体检谱面：声部、反复、速度、排练记号、占位字 | `analyze.py`、`check_all.py` | [workflow](references/workflow.md) |
| 2 | 校订副本、声部中文名与合并 | `tools/fix_mxl.py`、`tools/build_labels.py`、`labels.json` | [workflow](references/workflow.md) |
| 3 | 逐小节 DTW → **马上做逐起音细对齐** | `align/align.py`、`align/ssv_align.py` | [alignment](references/alignment.md) |
| 4 | 听检：争议片段合集；只在细对齐之后仍错的地方加钉点、局部重校，再重跑细对齐 | `demo/make_disputes.py`、`align/manual_regions.json`、`align/refine_local.py` | [alignment](references/alignment.md) |
| 5 | 曲式、动机、解析卡 | `form/`、`motifs.json`、`cards/` | [cards](references/cards.md) |
| 6 | 交叉核对卡片与谱面、审停留时间 | `tools/cards_xcheck.py`、`tools/cards_review.py` | [cards](references/cards.md) |
| 7 | 排谱、播放页、试片 | `demo/build_score.py`、`demo/player.html`、`demo/render_frames.py` | [rendering](references/rendering.md) |
| 8 | 全片渲染与拼接 | `demo/render_film.py` | [rendering](references/rendering.md) |

Windows 上踩过的坑（编码、进程、端口、批处理换行、ffmpeg 采样率等）集中在 [windows-notes](references/windows-notes.md)，动手前扫一遍。

## 最短路径（一个乐章）

```powershell
# 系统 Python（装了 verovio、music21、librosa、numba）
$py = "python"
& $py -X utf8 analyze.py                                   # 体检：out/<谱名>_structure.json、report.txt
& $py -X utf8 align/align.py --movement 1                  # ① 逐小节 DTW → align/m1_measures.json（开头锚点写在 align/anchors.json）
& $py -X utf8 align/ssv_align.py 1                         # ② 紧接着逐起音细对齐 → align/m1_ssv_chain.json
& $py -X utf8 align/make_checklist.py                      # 听检清单
& $py -X utf8 demo/make_disputes.py                        # ③ 争议片段合集：对着谱面听
# ④ 只在仍然不对的地方往 align/manual_regions.json 加 pins/beats，必要时：
& $py -X utf8 align/refine_local.py --movement 1
& $py -X utf8 align/ssv_align.py 1                         # ⑤ 改了钉点或小节表就重跑细对齐
& $py -X utf8 demo/build_score.py --movement 1 --range 1-40 --out demo/build   # 排谱（位置从 1 起）
& $py -X utf8 demo/render_frames.py --data build/data.js --out demo/test.mp4  # 试片
```

整片：

```powershell
& $py -X utf8 demo/render_film.py plan --res 1920x1080 --fps 60 --name 样片 --single 4
& $py -X utf8 demo/render_film.py worker 1 --only 4        # 可同时起多个 worker（编号决定调试端口）
& $py -X utf8 demo/render_film.py worker 2
& $py -X utf8 demo/render_film.py status
& $py -X utf8 demo/render_film.py assemble
```

长时间渲染要用与当前终端脱钩的进程启动（见 windows-notes 的 WMI 一节），否则关掉终端或会话就断了。

## 交付前自检

- 时间轴严格递增，没有缺小节；展开反复后的小节数与 `play_order` 一致。
- 声部名全部是中文，没有"未分类"。
- `cards_xcheck.py` 的可疑项逐条判断过；`cards_review.py` 全部通过；改过卡片后重跑 `build_refs.py`。
- 改了小节表或钉点后重跑 `ssv_align.py`（`build_score.py` 会用指纹拦下过期的细对齐）。
- 成片逐段核对帧数；抽帧目检。
