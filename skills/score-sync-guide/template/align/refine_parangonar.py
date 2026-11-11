"""困难段落的局部精修（用户 2026-11-07 定的正式流程：整体用 align.py 的逐小节 DTW，困难段落用 parangonar 校准）。

做法：
  1. 读 align.py 的结果 m<N>_measures.json，把有低置信度标记的小节连成段，两侧各多带 PAD 个可靠小节当"对照"。
  2. 只截这一小段录音、这一小段谱面交给 parangonar 的 AudioToScoreMatcher（窗口小，它跟踪拍长不会跑偏）。
  3. 四道检查都过才采用：
       a) 它对两侧对照小节的定位与 DTW 相差都在 ANCHOR_TOL 秒内（证明没跑偏）；
       b) 段内小节时刻严格递增；
       c) 段内各小节的速度不超过两侧对照小节平均速度的 1/2.5～2.5 倍；
       d) 段内每个小节精修后离录音里实际起音的距离，都不比 DTW 远（容差 20 毫秒）。
     不合格就保留 DTW 结果，并在日志里写明原因。
  4. 采用的小节改写 audio_sec，记下原值 audio_sec_dtw，加标记"已精修"；然后重算时长、速度，重写听检音频。
原 DTW 结果另存为 m<N>_measures_dtw.json（每次运行都从它出发，可重复运行）。

用法：python -X utf8 align/refine_parangonar.py --movement 3
"""
import argparse
import json
import pathlib
import sys
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "align"
sys.path.insert(0, str(OUT))

PAD = 2              # 段两侧各带几个可靠小节作对照
ANCHOR_TOL = 0.25    # 对照小节允许的偏差（秒）
TEMPO_RATIO = 2.5    # 段内速度相对两侧对照速度的上下限倍数
ONSET_SLACK = 0.02   # 检查 d：段内每个小节精修后离实际起音的距离，最多只能比 DTW 远这么多（秒）
WIN_PAD = 0.0        # 截录音时窗口起点不多留：parangonar 会把谱面第一个起音硬放在窗口起点上（2026-11-07 试验：
                     # 多留 1 秒时，各段对照小节都正好偏 1 秒）。所以窗口起点必须正好是第一个对照小节的开头
END_PAD = 0.3        # 窗口终点多留的秒数


def regions(rows: list) -> list[tuple[int, int, int, int]]:
    """返回 [(a, b, k0, k1)]：a..b 是要精修的低置信度小节，k0..k1 是加上两侧对照后的范围。"""
    bad = [bool(r["flags"]) for r in rows]
    out, k = [], 0
    while k < len(rows):
        if not bad[k]:
            k += 1
            continue
        j = k
        while j + 1 < len(rows) and bad[j + 1]:
            j += 1
        out.append((k, j, max(0, k - PAD), min(len(rows) - 1, j + PAD)))
        k = j + 1
    merged = []                                       # 对照范围重叠的段合并
    for r in out:
        if merged and r[2] <= merged[-1][3]:
            a, _, k0, _ = merged[-1]
            merged[-1] = (a, r[1], k0, r[3])
        else:
            merged.append(r)
    return merged


def main() -> None:
    import align as A
    import parangonar_align as PA
    import soundfile as sf
    from joblib import parallel_config
    from parangonar.match.audio_matchers import AudioToScoreMatcher

    ap = argparse.ArgumentParser()
    ap.add_argument("--movement", type=int, required=True)
    ap.add_argument("--after-local", action="store_true",
                    help="第二遍（2026-11-08）：在局部重校和人工钉点之后，从当前结果出发再核一遍；钉点不动，只在更贴近实际起音时采用")
    _a = ap.parse_args(); mv = _a.movement; AFTER = _a.after_local
    f = A.movement_files(mv)
    p_cur, p_dtw = OUT / f"m{mv}_measures.json", OUT / f"m{mv}_measures_dtw.json"
    res = json.loads(p_cur.read_text(encoding="utf-8"))
    pins = set()
    if AFTER:
        p_bk = OUT / f"m{mv}_measures_before_p2.json"
        if res.get("p2", {}).get("done"):
            res = json.loads(p_bk.read_text(encoding="utf-8"))
        p_bk.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        man = json.loads((OUT / "manual_regions.json").read_text(encoding="utf-8")).get(str(mv), {})
        pins = {int(k) for k in man.get("pins", {})}
    elif res.get("refine", {}).get("done"):                      # 已精修过：从 DTW 原结果重新开始
        res = json.loads(p_dtw.read_text(encoding="utf-8"))
    if not AFTER:
        p_dtw.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = res["measures"]
    regs = regions(rows)
    print(f"第 {mv} 乐章：{sum(1 for r in rows if r['flags'])} 个低置信度小节，合成 {len(regs)} 段")
    if not regs:
        return
    t0 = time.time()
    arr, _, _, _ = PA.score_note_array(f, mv)
    y, sr = sf.read(str(f["wav"]), dtype="float32")
    import librosa                                            # 录音里的实际起音，用于检查 d
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=128)
    onset_t = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=128, units="time")

    def dist(t):
        k = np.searchsorted(onset_t, t)
        return min(abs(onset_t[q] - t) for q in (k - 1, k) if 0 <= q < len(onset_t))

    matcher = AudioToScoreMatcher()
    log, n_acc = [], 0
    for a, b, k0, k1 in regs:
        q0 = rows[k0]["q_start"]; q1 = rows[k1]["q_start"] + rows[k1]["q_len"]
        sub = arr[(arr["onset_beat"] >= q0 - 1e-6) & (arr["onset_beat"] < q1 - 1e-6)]
        s0 = max(0.0, rows[k0]["audio_sec"] - WIN_PAD)
        s1 = min(len(y) / sr, rows[k1]["audio_sec"] + rows[k1]["audio_dur"] + END_PAD)
        tag = f"第 {rows[a]['measure']}–{rows[b]['measure']} 小节"
        if len(np.unique(sub["onset_beat"])) < 3:
            log.append(f"{tag}：谱面起音太少，跳过"); continue
        seg = y[int(s0 * sr):int(s1 * sr)]
        try:
            with parallel_config(backend="threading"):
                al = matcher(seg, sub, sample_rate=sr, audio_window=(0.0, len(seg) / sr))
        except Exception as e:                              # noqa: BLE001
            log.append(f"{tag}：parangonar 出错（{type(e).__name__}），保留 DTW"); continue
        id2q = {i: q for q, i in zip(sub["onset_beat"], sub["id"])}
        tq = {}
        for e in al:
            if e.get("label") == "match" and e.get("score_id") in id2q:
                tq.setdefault(id2q[e["score_id"]], []).append(float(e["performance_time"]) + s0)
        if len(tq) < 3:
            log.append(f"{tag}：匹配到的起音太少，保留 DTW"); continue
        Q = np.array(sorted(tq)); T = np.maximum.accumulate(np.array([np.median(tq[q]) for q in Q]))
        new = {k: float(np.interp(rows[k]["q_start"], Q, T)) for k in range(k0, k1 + 1)}
        # 检查 a）对照小节
        anchors = [k for k in range(k0, k1 + 1) if not (a <= k <= b) and k != k0]   # 第一个对照小节由窗口起点决定，不参与检验
        dev = [abs(new[k] - rows[k]["audio_sec"]) for k in anchors]
        if anchors and max(dev) > ANCHOR_TOL:
            log.append(f"{tag}：对照小节偏差 {max(dev):.2f} 秒 > {ANCHOR_TOL}，保留 DTW"); continue
        # 检查 b）递增
        seq = [new[k] for k in range(a, b + 1)]
        lo = rows[a - 1]["audio_sec"] if a > 0 else -1
        hi = rows[b + 1]["audio_sec"] if b + 1 < len(rows) else 1e9
        if not all(x < z for x, z in zip([lo] + seq, seq + [hi])):
            log.append(f"{tag}：精修后时间不递增，保留 DTW"); continue
        # 检查 c）速度
        ref = [rows[k]["bpm_quarter"] for k in anchors if rows[k]["bpm_quarter"]]
        ref_bpm = float(np.median(ref)) if ref else None
        ends = seq[1:] + [hi if hi < 1e9 else rows[b]["audio_sec"] + rows[b]["audio_dur"]]
        bpms = [60 * rows[k]["q_len"] / (e - s) for k, s, e in zip(range(a, b + 1), seq, ends) if e > s]
        if ref_bpm and any(not (ref_bpm / TEMPO_RATIO <= x <= ref_bpm * TEMPO_RATIO) for x in bpms):
            log.append(f"{tag}：精修后速度离谱（对照 {ref_bpm:.0f}，段内 {min(bpms):.0f}–{max(bpms):.0f}），保留 DTW"); continue
        # 检查 d）段内每个小节都不能比 DTW 离实际起音更远（2026-11-07：只检查对照小节时，谐谑曲第 1 小节曾被挪错 145 毫秒）
        worse = [rows[k]["measure"] for k in range(a, b + 1)
                 if dist(new[k]) > dist(rows[k]["audio_sec"]) + ONSET_SLACK]
        if worse:
            log.append(f"{tag}：第 {worse} 小节精修后离实际起音更远，整段保留 DTW"); continue
        if AFTER:
            if any(k in pins for k in range(a, b + 1)):
                log.append(f"{tag}：含人工钉点，不动"); continue
            if sum(dist(new[k]) for k in range(a, b + 1)) >= sum(dist(rows[k]["audio_sec"]) for k in range(a, b + 1)) - 0.01:
                log.append(f"{tag}：整体并不更贴近起音，保留"); continue
        shift = []
        for k in range(a, b + 1):
            r = rows[k]
            r["audio_sec_dtw"] = r["audio_sec"]; r["audio_sec"] = round(new[k], 3)
            r["flags"] = [x for x in r["flags"] if x != "已插值"] + (["已精修2"] if AFTER else ["已精修"])
            shift.append(1000 * (new[k] - r["audio_sec_dtw"]))
        n_acc += b - a + 1
        log.append(f"{tag}：采用（对照偏差最大 {max(dev) if dev else 0:.2f} 秒，挪动中位 {np.median(shift):+.0f} 毫秒）")
    for k, r in enumerate(rows):                              # 重算时长与速度
        if k + 1 < len(rows):
            r["audio_dur"] = round(rows[k + 1]["audio_sec"] - r["audio_sec"], 3)
        r["bpm_quarter"] = round(60 * r["q_len"] / r["audio_dur"], 1) if r["audio_dur"] > 0 else None
    if AFTER:
        res["p2"] = {"done": True, "measures_refined": n_acc, "log": log}
    else:
      res["refine"] = {"done": True, "method": "parangonar AudioToScoreMatcher 局部精修（见 align/refine_parangonar.py）",
                     "regions": len(regs), "measures_refined": n_acc, "log": log,
                     "params": {"PAD": PAD, "ANCHOR_TOL": ANCHOR_TOL, "TEMPO_RATIO": TEMPO_RATIO, "ONSET_SLACK": ONSET_SLACK}}
    times = [r["audio_sec"] for r in rows]
    res["checks"]["时间严格递增"] = all(z > x for x, z in zip(times, times[1:]))
    p_cur.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    A.write_check_wav(f, rows, mv)
    print("\n".join("  " + x for x in log))
    print(f"采用 {n_acc} 个小节，用时 {time.time() - t0:.0f} 秒；时间严格递增：{res['checks']['时间严格递增']}")


if __name__ == "__main__":
    main()
