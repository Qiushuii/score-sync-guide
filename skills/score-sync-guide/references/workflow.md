# 全流程与目录约定

## 目录

把 `template/` 整个复制到一个空的项目根目录，再放入素材：

```
项目根/
  manifest.json            乐章 ↔ 谱面 ↔ 录音对应表（格式见 examples/mahler6/manifest.json）
  <谱面>.mxl               原始 MusicXML，只读
  audio/                   原始录音，只读（不进版本库）
  scores_fixed/            校订副本 + corrections.json（改了什么、依据、原谱页码）
  labels.json              声部显示名、合并与隐藏（tools/build_labels.py 生成）
  form/m<N>.json           两级曲式结构（大段 sections、小段 subs，用谱面小节号，每段写 basis）
  motifs.json              动机与每次出现的位置（tools/build_motifs.py 生成）
  cards/m<N>.json          解析卡
  references.json          参考资料（refs）与要写进卡片的断言（claims）
  align/                   对齐脚本与结果
  demo/                    排谱、播放页、渲染
  tools/                   读谱、写卡、核对用的小工具
  out/                     analyze.py 与读谱工具的输出
  film/                    全片渲染的中间文件与成片
```

所有脚本都用"脚本所在目录的上一级"当项目根，所以必须保持这个层级。

## 两个编号别弄混

- **谱面小节号**（`measure_numbers`）：卡片、曲式、钉点说明里用它。有弱起的乐章常从 0 起。
- **位置**（`play_order` 里的索引，0 起）：展开反复后的演奏顺序。第一乐章有完整反复时，反复之后的位置和小节号不再相同。
- Verovio 的 `measureRange` 和 `build_score.py --range` 按**位置从 1 起**（位置 1 = 谱面第一个小节）。
- 对齐表 `align/m<N>_measures.json` 的**行号**区分反复的两遍；`build_score.py --first-row/--last-row/--anchor-row` 用行号。

## 1. 体检

`analyze.py` 输出 `report.txt` 和 `out/<谱名>_structure.json`：小节数、反复、展开后的演奏顺序、速度与拍号、排练记号、每个声部"在场"的小节区间（休止不超过 4 小节视为在场），以及中文声部名（规则在 `RULES`，可用 `names_override.csv` 覆盖）。

社区谱常见问题：
- 导入失败留下的占位字（如字面的 `other-dynamics`）：多是"力度 + 文字"的复合记号（`sempre ff`、`pp subito`）。对照原谱查出真记号，登记进 `scores_fixed/dynamics_pdf.json`，`tools/apply_dynamics_pdf.py` 转成修正条目。
- `ffp`、`sff` 这类 MusicXML 没有专门元素的记号会写成 `<other-dynamics>`，是真记号，不要删。
- 速度数据缺失或离谱（如 650 拍/分）：谱面速度只是对齐的先验，录音才是主时钟。
- 两行抄反、整段抄错行：`fix_mxl.py` 有 `move_measures`、`swap_measures` 两种整段搬移，不增删音符。

## 2. 声部

大编制的社区谱里，木管、铜管常是"几位乐手共用一行"（与印刷总谱一致），按行序编"小号 1–6"会错。做法：
- 共用行只写乐器名；谱面上的"1.2.3.""a 2"解析成乐手标记，播放器在声部名下用小字显示当前在奏的乐手。
- 单人乐器换调的两行合并；全空的谱表隐藏；竖琴、钢片琴的两行标"（上）""（下）"。
- 证据来自 `tools/part_audit.py`（每行的在场区间、乐手标记）和原谱首页编制；结论写进 `labels.json` 并向用户汇报。

## 3–4. 对齐与听检

见 [alignment.md](alignment.md)。

## 5–6. 曲式、动机、解析卡

见 [cards.md](cards.md)。

## 7–8. 排谱与渲染

见 [rendering.md](rendering.md)。
