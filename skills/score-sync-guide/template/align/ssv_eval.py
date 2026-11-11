"""检验 m<N>_ssv_chain.json 与现有结果哪个更贴近录音：用与两者都不同的信号——librosa 通用起音检测的峰——
比较谱面起音（同时刻 ≥2 个声部）的预计时刻到最近录音起音（±0.35 秒内）的距离。
另列出挪动最大的小节，供听检挑片段。
用法：python -X utf8 -W ignore align/ssv_eval.py <乐章>
"""
import bisect
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "align"))
from ssv_align import score_notes


def main():
    import librosa
    mv = int(sys.argv[1])
    rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
    man = json.loads((ROOT / "align" / "manual_regions.json").read_text(encoding="utf-8")).get(str(mv), {})
    ch = json.loads((ROOT / "align" / f"m{mv}_ssv_chain.json").read_text(encoding="utf-8"))
    guide = {}
    for k, r in enumerate(rows):
        guide[r["q_start"]] = r["audio_sec"]
        for off, t in man.get("beats", {}).get(str(k), []):
            guide[r["q_start"] + off] = t
    guide[rows[-1]["q_start"] + rows[-1]["q_len"]] = rows[-1]["audio_sec"] + rows[-1]["audio_dur"]
    gq = np.array(sorted(guide)); gt = np.array([guide[q] for q in gq])
    nq, nt = np.array(ch["q"]), np.array(ch["sec"])

    y, sr = librosa.load(str(ROOT / "wav" / f"m{mv}_mono22050.wav"), sr=22050)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
    on = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=256, units="time")

    def near(t):
        i = bisect.bisect_left(on, t)
        c = [abs(on[j] - t) for j in (i - 1, i) if 0 <= j < len(on)]
        m = min(c) if c else 9
        return m if m <= 0.35 else None

    bypos = {}
    for k, r in enumerate(rows):
        bypos.setdefault(r["position"], []).append(k)
    grp = {}
    for pos, off, dur, midi, pi, tie, grace in score_notes(mv):
        if tie or grace or dur <= 0:
            continue
        for k in bypos.get(pos, []):
            if off < rows[k]["q_len"]:
                grp.setdefault(rows[k]["q_start"] + off, set()).add(pi)
    old, new, per = [], [], {}
    for q, parts in grp.items():
        if len(parts) < 2:
            continue
        to, tn = np.interp(q, gq, gt), np.interp(q, nq, nt)
        do, dn = near(to), near(tn)
        if do is not None and dn is not None:
            old.append(do); new.append(dn)
    old, new = np.array(old), np.array(new)
    print(f"第 {mv} 乐章：{len(old)} 个起音。到最近通用起音的距离 中位 {np.median(old)*1000:.0f}→{np.median(new)*1000:.0f} 毫秒，"
          f"平均 {old.mean()*1000:.0f}→{new.mean()*1000:.0f}，40 毫秒内 {np.mean(old<.04)*100:.0f}%→{np.mean(new<.04)*100:.0f}%，"
          f"80 毫秒内 {np.mean(old<.08)*100:.0f}%→{np.mean(new<.08)*100:.0f}%；新的更近 {np.mean(new<old-.01)*100:.0f}%、更远 {np.mean(new>old+.01)*100:.0f}%")
    # 每小节：起点挪动、小节内最大挪动
    big = []
    for k, r in enumerate(rows):
        qq = np.linspace(r["q_start"], r["q_start"] + r["q_len"], 9)
        d = np.interp(qq, nq, nt) - np.interp(qq, gq, gt)
        big.append((float(abs(d).max()), k, r["measure"], r["audio_sec"], float(d[0])))
    big.sort(reverse=True)
    print("挪动最大的小节（行号 小节 录音时刻 最大挪动 起点挪动）：")
    for m, k, meas, a, d0 in big[:25]:
        print(f"  {k:4d} 第{meas:>4}小节 {int(a//60)}:{a%60:05.2f}  {m*1000:5.0f}  {d0*1000:+5.0f}")


if __name__ == "__main__":
    main()
