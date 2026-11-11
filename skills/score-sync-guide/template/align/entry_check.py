"""对齐复核（第二版，2026-11-08）：只用“听得出来”的进入音。

pitch_check.py 在织体密的地方不可靠。这里只挑这样的音：
  - 该声部前面至少休止一拍（新进入）；
  - 是同一时刻所有声部里最高的音（旋律线上），时值至少半拍；
对每个这样的音，在录音 CQT 里该音及高八度的频带上，于谱面时刻 ±0.8 秒内找能量上升的峰，
峰要明显高于这一窗口的中位数（> 中位数 + 4 × MAD），否则这个音不算数。
按相邻 4 个可用音为一组取偏差中位数；组内偏差相互一致（四分位距 < 0.3 秒）且 |中位数| > 0.25 秒才标出。
正数 = 录音里的音比谱面晚（谱面早了）。只作线索，标出的地方要人工逐 0.2 秒查看后再加钉点。

用法：python -X utf8 -W ignore align/entry_check.py <乐章> [首小节-末小节]
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
HOP, SR, W = 256, 22050, 0.8


def main():
    import librosa
    from similar_passages import notes
    mv = int(sys.argv[1])
    rng = [a for a in sys.argv[2:] if not a.startswith("--")]
    b0, b1 = (int(x) for x in rng[0].split("-")) if rng else (-1, 10 ** 6)
    rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
    bypos = {}
    for r in rows:
        bypos.setdefault(r["position"], r)
    ns = notes(mv)["notes"]
    # 每个声部的音按时间排序，找前面休止至少一拍的音
    qabs = lambda pos, off: bypos[pos]["q_start"] + off
    by_part = {}
    for pos, off, dur, midi, pi in ns:
        if pos in bypos:
            by_part.setdefault(pi, []).append((qabs(pos, off), dur, midi, pos, off))
    entries = []
    for pi, L in by_part.items():
        L.sort()
        prev_end = -1e9
        for q, dur, midi, pos, off in L:
            if q - prev_end >= 1.0 and dur >= 0.5:
                entries.append((q, midi, pos, off))
            prev_end = max(prev_end, q + dur)
    # 同一时刻的最高音
    top = {}
    for pos, off, dur, midi, pi in ns:
        if pos in bypos:
            k = round(qabs(pos, off), 2)
            top[k] = max(top.get(k, 0), midi)
    cand = []
    for q, midi, pos, off in entries:
        r = bypos[pos]
        if not (b0 <= r["measure"] <= b1) or midi < top[round(q, 2)]:
            continue
        cand.append((r["measure"], r["audio_sec"] + off / r["q_len"] * r["audio_dur"], midi))
    cand = sorted(set(cand), key=lambda x: x[1])
    if not cand:
        print("没有合适的进入音"); return
    y, _ = librosa.load(str(ROOT / "wav" / f"m{mv}_mono22050.wav"), sr=SR)
    C = np.abs(librosa.cqt(y, sr=SR, hop_length=HOP, fmin=librosa.midi_to_hz(36), n_bins=84))
    db = librosa.amplitude_to_db(C, ref=np.max)
    rise = np.maximum(0, np.diff(db, axis=1, prepend=db[:, :1]))
    rise = np.apply_along_axis(lambda v: np.convolve(v, np.ones(4) / 4, "same"), 1, rise)
    fps = SR / HOP
    good = []
    for m, t, midi in cand:
        i0, i1 = int((t - W) * fps), int((t + W) * fps)
        if i0 < 0 or i1 >= rise.shape[1]:
            continue
        v = sum(rise[b, i0:i1] for b in (midi - 36, midi - 24) if 0 <= b < rise.shape[0])
        med = np.median(v); mad = np.median(abs(v - med)) + 1e-6
        j = int(np.argmax(v))
        if v[j] > med + 4 * mad:
            good.append((m, t, i0 / fps + j / fps - t))
    print(f"第 {mv} 乐章：候选进入音 {len(cand)} 个，找到清楚起音的 {len(good)} 个。正数 = 谱面早了")
    if "--propose" in sys.argv:
        # 提议钉点：偏差 ≥ 0.45 秒、4 个音里至少 3 个同向且相差不大的组，给组里每个音所在的小节提议“起点 + 该音偏差”。
        # skip=乐章:首-末,…（用户已确认没问题的段落）不提议
        skip = []
        for a_ in sys.argv:
            if a_.startswith("--skip="):
                for s in a_[7:].split(","):
                    x, y_ = s.split("-"); skip.append((int(x), int(y_)))
        first_row = {}
        for i, r in enumerate(rows):
            first_row.setdefault(r["measure"], i)
        prop = {}
        for i in range(0, len(good) - 3, 2):
            g = good[i:i + 4]
            offs = np.array([x[2] for x in g]); med = float(np.median(offs))
            same = [x for x in g if abs(x[2] - med) < 0.2 and np.sign(x[2]) == np.sign(med)]
            if abs(med) >= 0.45 and len(same) >= 3:
                for m, t, o in same:
                    if any(a <= m <= b for a, b in skip):
                        continue
                    prop.setdefault(m, []).append(o)
        out = {str(first_row[m]): round(rows[first_row[m]]["audio_sec"] + float(np.median(v)), 3) for m, v in sorted(prop.items())}
        print("提议钉点（对齐表行号: 录音秒）：", json.dumps(out))
        return
    flagged = 0
    for i in range(0, len(good) - 3, 2):
        g = good[i:i + 4]
        offs = np.array([x[2] for x in g])
        med = float(np.median(offs)); iqr = float(np.subtract(*np.percentile(offs, [75, 25])))
        if abs(med) > 0.25 and iqr < 0.3:
            flagged += 1
            fmt = lambda s: f"{int(s // 60)}:{s % 60:04.1f}"
            print(f"  第 {g[0][0]}–{g[-1][0]} 小节（录音 {fmt(g[0][1])}）偏差中位 {med:+.2f} 秒  各音 {', '.join(f'{x:+.2f}' for x in offs)}")
    if not flagged:
        print("  没有发现一致的偏差")


if __name__ == "__main__":
    main()
