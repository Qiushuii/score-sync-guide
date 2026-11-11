"""按标志性声部的音高复核对齐（2026-11-08，用户：用寻找标志性声部和动机的方式重新校对）。

对指定声部的每个音：在录音的 CQT 里取这个音（及高八度）的频带，算“能量上升”曲线；
在 ±MAXLAG 秒内找让该声部所有音的能量上升之和最大的时间差。逐小节输出最佳时间差：
正数 = 录音里的音比谱面（当前对齐）晚，即谱面早了；负数 = 谱面晚了。
只作线索：同音高的其他乐器、长音、颤音都会干扰。结果要与听感和其他声部互相印证再加钉点。

用法：python -X utf8 -W ignore align/pitch_check.py <乐章> <首小节>-<末小节> <声部英文名片段>[,<片段>...] [--win 3]
  --win：按几个小节为一组求时间差（默认 2）
"""
import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
MAXLAG = 1.5
HOP = 256
SR = 22050


def main():
    import librosa
    from similar_passages import notes
    from motif_scan import parts_en
    ap = argparse.ArgumentParser()
    ap.add_argument("mv", type=int)
    ap.add_argument("bars")
    ap.add_argument("parts")
    ap.add_argument("--win", type=int, default=2)
    a = ap.parse_args()
    b0, b1 = (int(x) for x in a.bars.split("-"))
    keys = [k.strip().lower() for k in a.parts.split(",")]
    en, _ = parts_en(a.mv)
    rows = json.loads((ROOT / "align" / f"m{a.mv}_measures.json").read_text(encoding="utf-8"))["measures"]
    bypos = {}
    for r in rows:                       # 反复的小节取第一遍
        bypos.setdefault(r["position"], r)
    evs = []                             # (小节号, 谱面时刻, midi)
    for pos, off, dur, midi, pi in notes(a.mv)["notes"]:
        r = bypos.get(pos)
        if r is None or not (b0 <= r["measure"] <= b1) or not any(k in en[pi].lower() for k in keys):
            continue
        evs.append((r["measure"], r["audio_sec"] + off / r["q_len"] * r["audio_dur"], midi))
    if not evs:
        print("没有找到这些声部的音"); return
    t_lo = min(e[1] for e in evs) - MAXLAG - 1
    t_hi = max(e[1] for e in evs) + MAXLAG + 1
    y, _ = librosa.load(str(ROOT / "wav" / f"m{a.mv}_mono22050.wav"), sr=SR, offset=max(0, t_lo), duration=t_hi - t_lo)
    fmin = librosa.midi_to_hz(36)
    C = np.abs(librosa.cqt(y, sr=SR, hop_length=HOP, fmin=fmin, n_bins=72))
    db = librosa.amplitude_to_db(C, ref=np.max)
    rise = np.maximum(0, np.diff(db, axis=1, prepend=db[:, :1]))
    # 平滑一点，起音宽度约 60 毫秒
    k = np.ones(5) / 5
    rise = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 1, rise)
    t_axis = max(0, t_lo) + np.arange(C.shape[1]) * HOP / SR
    lags = np.arange(-MAXLAG, MAXLAG + 1e-9, 0.02)

    def curve(group):
        tot = np.zeros(len(lags))
        for _, t, m in group:
            for mm in (m, m + 12):
                bi = mm - 36
                if 0 <= bi < C.shape[0]:
                    idx = np.searchsorted(t_axis, t + lags)
                    idx = np.clip(idx, 0, len(t_axis) - 1)
                    tot += rise[bi, idx]
        return tot / max(1, len(group))

    bars = sorted({e[0] for e in evs})
    print(f"第 {a.mv} 乐章 {a.parts}：{len(evs)} 个音。正数 = 谱面早了（录音更晚）")
    for i in range(0, len(bars), a.win):
        bs = bars[i:i + a.win]
        g = [e for e in evs if e[0] in bs]
        cv = curve(g)
        j = int(np.argmax(cv)); best = lags[j]
        zero = cv[np.argmin(abs(lags))]
        conf = cv[j] / (np.median(cv) + 1e-6)
        mark = "  ←" if abs(best) > 0.25 and conf > 1.5 else ""
        print(f"第 {bs[0]:>4}–{bs[-1]:<4} 小节 音 {len(g):3d}  最佳时间差 {best:+.2f} 秒  显著度 {conf:4.1f}{mark}")


if __name__ == "__main__":
    main()
