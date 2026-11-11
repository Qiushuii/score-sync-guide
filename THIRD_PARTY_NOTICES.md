# 第三方组件与致谢 / Third-party notices

## 随仓库分发 / Bundled

### score-sync-video

- 来源 / Source: https://github.com/tofucurd/score-sync-video
- 文件 / Files: `skills/score-sync-guide/template/align/vendor_ssv/scripts/score_sync/alignment.py`, `alignment_pipeline.py`（未改动 / unmodified；`__init__.py` 为空文件 / empty）
- 许可证 / License: MIT, Copyright (c) 2026 tofucurd — 全文见 / full text in `skills/score-sync-guide/template/align/vendor_ssv/LICENSE`
- 用途 / Use: 逐起音细对齐（`dense_sequence`、`correct_guide`、`refine_attack_chain`、`extract_features`、`pitch_bounds`）

## 运行时依赖（不随仓库分发） / Runtime dependencies (not bundled)

| 组件 | 用途 | 许可证（以各项目官方为准） |
|---|---|---|
| [Verovio](https://www.verovio.org/) | 排谱、导出时间表 | LGPL-3.0 |
| [music21](https://web.mit.edu/music21/) | 读谱、实际音高、动机搜索 | BSD-3-Clause |
| [librosa](https://librosa.org/) | 音频特征、DTW、起音检测 | ISC |
| NumPy / SciPy / Numba | 数值计算 | BSD |
| [partitura](https://github.com/CPJKU/partitura)（可选） | 音符表、调性估计 | Apache-2.0 |
| [parangonar](https://github.com/sildater/parangonar)（可选） | 局部逐音符精修 | Apache-2.0 |
| [FFmpeg](https://ffmpeg.org/) | 编码、拼接、音频处理 | LGPL/GPL（视构建而定） |
| Microsoft Edge | 无头渲染 | 专有软件，系统自带 |
