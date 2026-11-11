"""用 parangonar 的 AudioToScoreMatcher 做逐起音的音频—乐谱对齐，并与现有逐小节结果（align.py）对比。

parangonar（github.com/sildater/parangonar，Apache-2.0）：把谱面上每一个"起音时刻"（同一时刻开始的一组音）
放进录音里，同时看这组音高的频谱是否吻合、录音在那里有没有起音，并跟踪一拍的长度（速度要连续合理）。
输出每个起音时刻在录音里的秒数——比逐小节、小节内按比例插值更细。

输入：谱面音符表（自己用 music21 生成：实际音高、按演奏顺序的四分音符位置、唯一 id），
      分析用 WAV，起止窗口（起点用 align/anchors.json，终点用现有对齐结果的最后一小节结束处 + 5 秒）。
输出：align/m<N>_onsets_parangonar.json（每个起音时刻：q 位置、录音秒数），
      align/m<N>_measures_parangonar.json（与 align.py 同格式的逐小节结果，方便播放器直接换用），
      屏幕上打印与现有结果的差异统计。

用法：python -X utf8 align/parangonar_align.py --movement 2
"""
import argparse
import json
import pathlib
import sys
import time
import types
import warnings

import numpy as np

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "align"
sys.path.insert(0, str(OUT))

# parangonar 3.4.1 的包初始化会导入 tqdm，但安装清单漏了这个依赖。
# 用户未装 tqdm 时放一个空替身（它只在本脚本用不到的另一个匹配器里显示进度条），装了就用真的。
try:
    import tqdm  # noqa: F401
except ImportError:
    _stub = types.ModuleType("tqdm")
    _stub.tqdm = lambda it=None, *a, **k: it
    sys.modules["tqdm"] = _stub

from parangonar.match.audio_matchers import AudioToScoreMatcher  # noqa: E402


def score_note_array(f: dict, mv: int):
    """谱面音符表：实际音高（MIDI）、按演奏顺序的四分音符位置（与 align.py 的 q_start 同一坐标）。"""
    import music21 as m21
    st = json.loads(f["structure"].read_text(encoding="utf-8"))
    order = st["play_order"]
    score = m21.converter.parse(str(f["score"])).toSoundingPitch()
    parts = list(score.parts)
    meas_lists = [list(p.getElementsByClass("Measure")) for p in parts]
    qlen = [max(float(ml[pos].duration.quarterLength) for ml in meas_lists if pos < len(ml)) for pos in order]
    qstart = np.concatenate([[0.0], np.cumsum(qlen)])[:-1]
    rows = []
    for pi, ml in enumerate(meas_lists):
        for k, pos in enumerate(order):
            if pos >= len(ml):
                continue
            meas = ml[pos]
            for n in meas.recurse().notes:
                tie = n.tie.type if n.tie is not None else None
                if tie in ("stop", "continue"):                 # 延音线后半段不是新起音
                    continue
                for p in getattr(n, "pitches", ()):
                    midi = int(round(p.ps))
                    if 21 <= midi <= 108:                       # 匹配器的频率范围是钢琴 88 键
                        q = qstart[k] + float(n.getOffsetInHierarchy(meas))
                        rows.append((round(q, 4), midi, f"p{pi}k{k}n{len(rows)}"))
    arr = np.array(rows, dtype=[("onset_beat", "f8"), ("pitch", "i4"), ("id", "U32")])
    arr.sort(order="onset_beat")
    return arr, qstart, qlen, order


def main() -> None:
    import align as A                                           # 复用 align.py 的文件定位
    import soundfile as sf
    ap = argparse.ArgumentParser()
    ap.add_argument("--movement", type=int, required=True)
    args = ap.parse_args()
    mv = args.movement
    f = A.movement_files(mv)
    t0 = time.time()
    arr, qstart, qlen, order = score_note_array(f, mv)
    print(f"谱面：{len(arr)} 个音、{len(np.unique(arr['onset_beat']))} 个起音时刻")

    cur = json.loads((OUT / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
    anc = json.loads((OUT / "anchors.json").read_text(encoding="utf-8")).get(str(mv))
    start = (anc["audio_sec"] if anc else cur[0]["audio_sec"]) - 0.3
    end = cur[-1]["audio_sec"] + cur[-1]["audio_dur"] + 5.0
    y, sr = sf.read(str(f["wav"]), dtype="float32")
    print(f"录音窗口 {start:.2f}–{end:.2f} 秒，开始匹配（第一次运行要编译，稍慢）")
    matcher = AudioToScoreMatcher()
    # parangonar 内部用 joblib 并行计算频谱；joblib 默认开子进程，子进程重新导入 parangonar 时没有 tqdm 替身会失败。
    # 改用线程，替身对所有线程都有效（用户装了 tqdm 之后也不受影响）。
    from joblib import parallel_config
    with parallel_config(backend="threading"):
        al = matcher(y, arr, sample_rate=sr, audio_window=(max(0.0, start), min(end, len(y) / sr)))
    # 每个起音时刻 → 录音秒数（同一时刻的多个音取中位数）
    t_of = {}
    id2q = {i: q for q, i in zip(arr["onset_beat"], arr["id"])}
    for e in al:
        if e.get("label") == "match" and e.get("score_id") in id2q:
            t_of.setdefault(id2q[e["score_id"]], []).append(float(e["performance_time"]))
    Q = np.array(sorted(t_of)); T = np.array([np.median(t_of[q]) for q in Q])
    T = np.maximum.accumulate(T)
    print(f"匹配到 {len(Q)} 个起音时刻，用时 {time.time() - t0:.0f} 秒")
    (OUT / f"m{mv}_onsets_parangonar.json").write_text(
        json.dumps({"movement": mv, "method": "parangonar AudioToScoreMatcher（默认参数）",
                    "onsets": [[round(float(q), 4), round(float(t), 3)] for q, t in zip(Q, T)]}, ensure_ascii=False),
        encoding="utf-8")

    # 逐小节：小节开头若正好有起音就用它的时刻，否则在相邻起音之间按四分音符插值
    rows = []
    for k, r in enumerate(cur):
        q = r["q_start"]
        t = float(np.interp(q, Q, T))
        rows.append({**r, "audio_sec": round(t, 3), "source": "parangonar"})
    for k, r in enumerate(rows):
        r["audio_dur"] = round((rows[k + 1]["audio_sec"] if k + 1 < len(rows) else r["audio_sec"] + r["audio_dur"]) - r["audio_sec"], 3)
        r["bpm_quarter"] = round(60 * r["q_len"] / r["audio_dur"], 1) if r["audio_dur"] > 0 else None
    out = {"movement": mv, "method": "parangonar AudioToScoreMatcher（逐起音），小节开头取对应起音或插值",
           "status": "试验：未经人工核对", "audio": str(f["audio"].relative_to(ROOT)), "measures": rows}
    (OUT / f"m{mv}_measures_parangonar.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    d = np.array([b["audio_sec"] - a["audio_sec"] for a, b in zip(cur, rows)]) * 1000
    print(f"与现有逐小节结果相比（parangonar − 现有）：中位数 {np.median(d):+.0f} 毫秒，"
          f"四分位 {np.percentile(d, 25):+.0f}～{np.percentile(d, 75):+.0f} 毫秒，"
          f"相差超过 0.5 秒的小节 {int(np.sum(np.abs(d) > 500))} 个 / {len(d)}")
    big = [(r["measure"], round(x)) for r, x in zip(rows, d) if abs(x) > 500][:15]
    if big:
        print("  相差大的小节（小节号, 毫秒）：", big)


if __name__ == "__main__":
    main()
