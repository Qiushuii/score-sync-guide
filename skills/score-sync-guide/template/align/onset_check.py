"""对齐复核：把谱面上每个起音按当前对齐结果换成录音时刻，与录音里检测到的起音比较，逐小节给出偏差中位数。
偏差为正 = 谱面上的音比录音里的音晚出现（谱面慢），为负 = 谱面早。只统计 ±0.6 秒内找得到起音的音，
并要求同一小节至少 4 个音参与。结果只作线索（弱奏、长音、混响多的地方起音检测不可靠）。
用法：python -X utf8 -W ignore align/onset_check.py <乐章> <首行>-<末行> [声部名片段，如 Violoncell]
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def main():
    import librosa
    from similar_passages import notes
    mv = int(sys.argv[1]); a, b = (int(x) for x in sys.argv[2].split("-"))
    part = sys.argv[3] if len(sys.argv) > 3 else None          # 只看某个声部（英文名片段，如 Violoncell），录音用低频段起音
    from motif_scan import parts_en
    en, _ = parts_en(mv)
    rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
    d = notes(mv)
    by = {}
    for pos, off, dur, midi, pi in d["notes"]:
        if part and part.lower() not in en[pi].lower():
            continue
        by.setdefault(pos, set()).add(round(off, 3))
    T0 = rows[a]["audio_sec"] - 1; T1 = rows[b]["audio_sec"] + rows[b]["audio_dur"] + 1
    y, sr = librosa.load(str(ROOT / "wav" / f"m{mv}_mono22050.wav"), sr=22050, offset=T0, duration=T1 - T0)
    if part:                                                   # 低音声部：只看 400 Hz 以下
        S = np.abs(librosa.stft(y, n_fft=2048, hop_length=128)); f = librosa.fft_frequencies(sr=sr, n_fft=2048)
        env = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[f < 400]), sr=sr, hop_length=128)
    else:
        env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=128)
    on = T0 + librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=128, units="time")
    for k in range(a, b + 1):
        r = rows[k]
        offs = sorted(by.get(r["position"], ()))
        ts = [r["audio_sec"] + o / r["q_len"] * r["audio_dur"] for o in offs if o < r["q_len"]]
        dev = []
        for t in ts:
            j = np.searchsorted(on, t)
            c = [on[q] - t for q in (j - 1, j) if 0 <= q < len(on)]
            if c:
                x = min(c, key=abs)
                if abs(x) < 0.6:
                    dev.append(-x)          # 正 = 谱面晚
        med = float(np.median(dev)) if len(dev) >= 4 else None
        mark = "  ←" if med is not None and abs(med) > 0.2 else ""
        print(f"第 {r['measure']:>4} 小节 {r['audio_sec']:8.2f}s 长 {r['audio_dur']:5.2f}  音 {len(ts):3d} 匹配 {len(dev):3d}  "
              f"偏差 {'—' if med is None else f'{med:+.2f}'}{mark}")


if __name__ == "__main__":
    main()
