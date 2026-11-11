<div align="center">

# 跟谱听 · score-sync-guide

**简体中文** | [English](README.en.md)

在 Windows 上，把 MusicXML 总谱和一份录音做成"谱面同步滚动 + 导赏解析"的长视频。<br>
附带一个 Claude Code skill，把整套做法（对齐、排谱、渲染、写卡、核对）交给 AI 助手照着做。

</div>

## 这是什么

画面左边是一行横向滚动的总谱：播放线固定不动，谱子从右往左走；正在发声的音符变红；只显示正在演奏的声部，声部进出时淡入淡出，全奏时整份谱面像镜头拉远一样缩小。右边是导赏解析卡（曲式、主题、配器、表情记号，带注释角标）和本乐章的曲式结构。乐章之间有过渡动画，片尾列出参考资料。

这套流程来自一部马勒《第六交响曲》（四个乐章、约 83 分钟）导赏视频的实际制作。仓库里的代码就是那次制作用的代码，数据以它为例（`examples/mahler6/`）。

## 与 score-sync-video 的关系（致谢）

本项目参考并移植了 [**tofucurd/score-sync-video**](https://github.com/tofucurd/score-sync-video)（MIT 许可证）：

- **逐起音对齐算法直接来自它。** `align/vendor_ssv/` 里原样复制了它的 `scripts/score_sync/alignment.py` 和 `alignment_pipeline.py`（来源提交号见 `SOURCE.txt`，许可证见同目录 `LICENSE`）。我们的 `align/ssv_align.py` 调用其中的 `dense_sequence`、`correct_guide`、`refine_attack_chain`，用自己的逐小节对齐结果作引导、人工钉点作精确约束。
- 播放器的几项设计借鉴了它：固定游标、稳定的声部集合、两级曲式结构、中文声部名表、分块可续渲。

score-sync-video 不支持原生 Windows。本项目可以看作它在 **Windows 上的本地化**，并加了一些它没有的东西：

| | 本项目 |
|---|---|
| 平台 | 原生 Windows 11；不需要 WSL、Node、Playwright。无头 Edge + Python 标准库实现的 DevTools 客户端 |
| 对齐 | 逐小节 DTW → 逐起音细对齐（score-sync-video 算法）→ 对着谱面听检 → 只在仍错处加人工钉点，再重跑细对齐 |
| 听检 | "争议片段合集"：把可疑段渲成带谱面、带录音的短片，不识谱的人也能对着谱面听 |
| 内容 | 导赏解析卡、曲式卡、动机色带、注释角标与片尾参考资料 |
| 核对 | 卡片与谱面交叉核对（乐器有没有音、记号写在哪个声部、力度、调号、拍号、声部数） |
| 渲染 | 一个乐章只排一次谱、多进程按帧号分段共用，接缝无痕；NVENC 编码；可选从完整演出视频的原始音轨截取 24 位 / 48 kHz 声音 |

## 少走弯路

我们走过的弯路记在 [`references/alignment.md`](skills/score-sync-guide/references/alignment.md) 的"试过、不推荐"一节。最重要的一条：**逐小节对齐之后马上跑逐起音细对齐，再听、再加钉点。** 我们当初先听了八轮、加了近百个钉点和拍点，最后才引入细对齐，它一次就解决了大部分小节内的偏移。

Windows 上的坑（编码、PowerShell、批处理换行、长时间渲染的进程、采样率）见 [`references/windows-notes.md`](skills/score-sync-guide/references/windows-notes.md)。

## 目录

```
skills/score-sync-guide/
  SKILL.md                给 Claude Code 的 skill 说明（流程、规矩、命令）
  references/             分步骤的详细笔记：workflow / alignment / rendering / cards / windows-notes
  template/               代码：复制到你的项目根目录使用
    analyze.py            谱面体检
    align/                对齐（含 vendor_ssv/，来自 score-sync-video）
    demo/                 排谱、播放页、单段试片、整片渲染、争议片段合集、片头片尾
    tools/                校订副本、声部表、读谱工具、写卡与核对工具
examples/mahler6/         马勒六的示例数据：解析卡、曲式、动机、声部表、参考资料、对齐锚点与钉点、校订记录
```

## 环境

- Windows 10/11，Microsoft Edge（系统自带）
- Python（本项目用 3.15）：`verovio`、`music21`、`librosa`、`numpy`、`scipy`、`numba`；可选 `partitura`、`parangonar`、`tqdm`。见 `requirements.txt`。
- ffmpeg（需要在 PATH 里）；用 NVIDIA 显卡编码时需要较新的驱动。

## 用法

### 作为 Claude Code skill

把 `skills/score-sync-guide/` 复制到 `~/.claude/skills/`（或项目的 `.claude/skills/`），然后在 Claude Code 里描述你要做的视频即可，例如"用这份 mxl 和这个录音做一个跟谱听视频"。

### 直接用脚本

1. 新建项目目录，把 `template/` 里的内容复制进去，放入你的 `.mxl` 和录音（录音放 `audio/`）。
2. 照 `examples/mahler6/manifest.json` 写 `manifest.json`。
3. 按 `skills/score-sync-guide/SKILL.md` 的"最短路径"一步步跑。

代码里有不少马勒六专用的常量（乐章标题、反复位置、片头文字等），用于别的作品时要改；它们都在脚本开头或有注释标明。

## 不包含的东西

为了版权，仓库里**没有**：任何录音或视频、MusicXML 谱面及其校订副本、原谱 PDF、对齐用的音频缓存、渲染出的成片，以及第三方商标图片。片尾的标志图片缺失时会自动隐藏。

## 说明

- 本项目在 Claude Code 协助下完成，代码注释与笔记多为中文。
- 小节号按所用谱面；社区 MusicXML 可能有个别错误，与印刷总谱也可能不是同一版本。
- 示例数据中的分析文字是为那支视频写的导赏，出处见 `examples/mahler6/references.json`。

## 许可证

MIT，见 [LICENSE](LICENSE)。`align/vendor_ssv/` 中的文件版权归 tofucurd，按其 MIT 许可证分发。其他依赖见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
