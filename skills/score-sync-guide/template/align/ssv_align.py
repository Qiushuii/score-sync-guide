"""试验：借用 tofucurd/score-sync-video（MIT）的两步细对齐，在我们现有的逐小节对齐之上做小节内（逐起音）校准。

只借它的算法函数（scripts/score_sync/alignment_pipeline.py 的 dense_sequence、refine_attack_chain，
alignment.py 的 extract_features），谱面、引导路线、钉点都用我们自己的：
- 引导：align/m<N>_measures.json 的 q_start→audio_sec（含 manual_regions.json 的小节内 beats）；
- 约束：manual_regions.json 的 pins、beats，以及每乐章起点锚点，全部当作"精确"约束，不许挪；
- 谱面：music21 读校订副本，按实际音高，去掉延音线的后半、倚音。
它的两步：
  1. 10 毫秒网格上的"状态+起音"带状 DTW（谱面步长 1/128–1/8 个四分音符，局部速度 0.4–2.5 倍，带宽 ±3 秒）；
  2. 逐起音的"有序音高链"：每个起音在 ±2 秒内找候选峰，相邻起音间隔偏离附近实际速度时加罚 4·ln²，整条链一起求最优。
输出（不覆盖任何现有结果）：align/m<N>_ssv_chain.json（每个起音的 q 与录音秒），并打印与现有结果的差别。
录音只在本机读取。
用法：python -X utf8 -W ignore align/ssv_align.py <乐章>   （算法文件默认取项目里的 align/vendor_ssv/，见其中 SOURCE.txt）
"""
import argparse
import bisect
import json
import os
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
if os.environ.get("FFMPEG_DIR"):   # ffmpeg 不在 PATH 里时，设环境变量 FFMPEG_DIR 指向它的 bin 目录
    os.environ["PATH"] = os.environ["FFMPEG_DIR"] + os.pathsep + os.environ.get("PATH", "")


def _jload(name):
    """align/ 下的 JSON；文件还没建（例如还没加任何钉点）时当作空。"""
    f = ROOT / "align" / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def inputs_digest(mv):
    """引导与约束的指纹：m<N>_measures.json + manual_regions.json 与 anchors.json 里本乐章那一项。
    build_score.py 用同样算法核对，钉点或小节表改了而没重跑本脚本时拒绝使用旧时间表。"""
    import hashlib
    h = hashlib.sha256((ROOT / "align" / f"m{mv}_measures.json").read_bytes())
    for f in ("manual_regions.json", "anchors.json"):
        d = _jload(f).get(str(mv), {})
        h.update(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def score_notes(mv):
    """[pos, off, dur, midi, part, tie_stop]，缓存到 out/notes_ties_m<N>.json。"""
    cache = ROOT / "out" / f"notes_ties_m{mv}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import warnings
    warnings.filterwarnings("ignore")
    import music21 as m21
    man = {m["id"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["movements"]}
    src = ROOT / "scores_fixed" / man[mv]["score"]
    if not src.exists():
        src = ROOT / man[mv]["score"]
    sc = m21.converter.parse(str(src)).toSoundingPitch()
    out = []
    for pi, p in enumerate(sc.parts):
        for pos, m in enumerate(p.getElementsByClass("Measure")):
            for n in m.recurse().notes:
                if not hasattr(n, "pitches") or (not n.isChord and not hasattr(n, "pitch")):
                    continue
                if n.isChord and any(not hasattr(x, "pitch") for x in n.notes):
                    continue
                ps = n.pitches if n.isChord else (n.pitch,)
                notes = n.notes if n.isChord else (n,)
                for x, nn in zip(ps, notes):
                    tie = nn.tie if nn.tie is not None else n.tie
                    ts = tie is not None and tie.type in ("stop", "continue")
                    out.append([pos, float(n.getOffsetInHierarchy(m)), float(n.duration.quarterLength),
                                int(round(x.ps)), pi, ts, bool(n.duration.isGrace)])
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mv", type=int)
    ap.add_argument("--ssv", default=str(ROOT / "align" / "vendor_ssv"), help="score-sync-video 目录（默认用项目里复制的两个文件）")
    ap.add_argument("--step-q", type=float, default=None)
    ap.add_argument("--tempo-weight", type=float, default=4.0)
    a = ap.parse_args()
    sys.path.insert(0, str(pathlib.Path(a.ssv) / "scripts"))
    from score_sync.alignment import pitch_bounds, extract_features
    from score_sync.alignment_pipeline import dense_sequence, refine_attack_chain, correct_guide

    mv = a.mv
    rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
    man = _jload("manual_regions.json").get(str(mv), {})
    anchors = _jload("anchors.json")
    bypos = {}
    for k, r in enumerate(rows):
        bypos.setdefault(r["position"], []).append(k)

    events = []
    for pos, off, dur, midi, pi, tie_stop, grace in score_notes(mv):
        for k in bypos.get(pos, []):
            r = rows[k]
            if off >= r["q_len"] - 1e-6:
                continue
            events.append(dict(q=r["q_start"] + off, duration=dur, pitch=midi, tie_stop=tie_stop,
                               grace=grace or dur <= 0, is_player=True, row=k))
    events.sort(key=lambda e: e["q"])

    # 引导路线 = 现有结果（小节起点 + 人工小节内拍点）
    guide = {}
    for k, r in enumerate(rows):
        guide[r["q_start"]] = r["audio_sec"]
        for off, t in man.get("beats", {}).get(str(k), []):
            guide[r["q_start"] + off] = t
    last = rows[-1]
    guide[last["q_start"] + last["q_len"]] = last["audio_sec"] + last["audio_dur"]
    gq = np.array(sorted(guide)); gt = np.array([guide[q] for q in gq])

    # 精确约束：钉点、拍点、起点锚点
    cons = {}
    def pin(q, t, why):
        cons[float(q)] = dict(seconds=float(t), tolerance_seconds=.015, exact=True, why=why)
    for k, t in man.get("pins", {}).items():
        pin(rows[int(k)]["q_start"], t, "pin")
    for k, bl in man.get("beats", {}).items():
        for off, t in bl:
            pin(rows[int(k)]["q_start"] + off, t, "beat")
    an = anchors.get(str(mv))
    if an:
        for k, r in enumerate(rows):
            if r["measure"] == an["measure"]:
                pin(r["q_start"], r["audio_sec"], "anchor"); break

    lo, hi = pitch_bounds(events)
    work = ROOT / "align" / "cache" / f"ssv_m{mv}"
    work.mkdir(parents=True, exist_ok=True)
    mf = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    audio = ROOT / [m for m in mf["movements"] if m["id"] == mv][0]["audio"]
    fz = work / f"features_{lo}_{hi}.npz"
    names = ["ts", "rise", "change", "ct", "cqt", "rms"]
    if fz.exists():
        with np.load(fz) as z:
            feat = {n: z[n] for n in names}
    else:
        feat = dict(zip(names, extract_features(audio, work, 2, lo, hi)))
        np.savez_compressed(fz, **feat)
        (work / "analysis-24k.f32").unlink(missing_ok=True)
    print(f"音高范围 {lo}–{hi}，起音 {sum(1 for e in events if not e['tie_stop'] and not e['grace'])}，约束 {len(cons)}", flush=True)

    config = {"tempo_weight": a.tempo_weight, "max_backtrack_cells": 200_000_000}
    if a.step_q:
        config["step_q"] = a.step_q
    import time
    t0 = time.time()
    qd, td, par = dense_sequence({"events": events}, feat, lo, hi, gq, gt, cons, config)
    print(f"细 DTW：{time.time()-t0:.0f} 秒，{par}", flush=True)
    td = correct_guide(qd, td, cons)
    qs, tt, chain = refine_attack_chain(events, feat["ts"], feat["rise"], lo, qd, td, cons, config)
    print(f"起音链：{time.time()-t0:.0f} 秒，{len(qs)} 个起音位置", flush=True)
    # 精确约束落在非起音位置的，事后插回
    qq = list(qs); tl = list(tt)
    for q, c in cons.items():
        i = bisect.bisect_left(qq, q)
        if i < len(qq) and abs(qq[i] - q) < 1e-9:
            tl[i] = c["seconds"]
        else:
            qq.insert(i, q); tl.insert(i, c["seconds"])
    bad = sum(1 for x, y in zip(tl, tl[1:]) if y <= x)
    out = dict(movement=mv, inputs=inputs_digest(mv), method="score-sync-video dense state/attack DTW + ordered pitch-attack chain, guided by our measure map, our pins exact",
               params={**par, **config}, q=[round(x, 5) for x in qq], sec=[round(x, 4) for x in tl], nonmonotone=bad)
    p = ROOT / "align" / f"m{mv}_ssv_chain.json"
    p.write_text(json.dumps(out), encoding="utf-8")

    # 与现有结果比较：小节起点挪了多少
    qa, ta = np.array(qq), np.array(tl)
    sh = np.array([np.interp(r["q_start"], qa, ta) - np.interp(r["q_start"], gq, gt) for r in rows])
    # 所有起音
    sha = ta - np.interp(qa, gq, gt)
    print(f"非单调 {bad}；小节起点挪动 |Δ| 中位 {np.median(abs(sh))*1000:.0f} 毫秒、90% {np.percentile(abs(sh),90)*1000:.0f}、最大 {abs(sh).max()*1000:.0f}；"
          f"全部起音 |Δ| 中位 {np.median(abs(sha))*1000:.0f}、90% {np.percentile(abs(sha),90)*1000:.0f} 毫秒 → {p.name}")


if __name__ == "__main__":
    main()
