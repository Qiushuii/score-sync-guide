"""局部重校（2026-11-07，用户听争议片段合集后要求：所有可疑段落都再校一遍）。

起因：用户听出的问题多半来自 align.py 最后一步"插值"——可疑小节被按谱面时值比例平均分配，
结果出现 1 秒的小节（第二乐章第 196 小节"被光速跳过"）、前后都 5 秒而中间 1.97 秒（第一乐章第 117 小节）、
整段速度被抹平（第一乐章第 231–238、第二乐章第 55–59 小节）等。

做法：对每个可疑段落（低置信度小节连成的段，加上 align/manual_regions.json 里人工指定的段），
取两侧各 MARG 个小节作端点（端点小节的起点固定不动），只在这一小段录音里重新对齐：
  - 特征比原来细：录音用 84 个半音的 CQT（C1–B7，不折叠八度）+ 十二音级色度 + 起音强度；
    谱面按音符实际音高画模板（基音 + 2、3、4、5 次泛音，音符结束后约 1 秒余响）。
  - DTW 两端固定，局部斜率限制在 1/3～3 倍（相对这一小段的平均速度），不会出现一瞬间跳过一小节。
  - 不再插值。新结果在同一套特征下的平均匹配代价比旧结果低，才采用；否则保留旧结果。
采用的小节：audio_sec 改为新值，旧值记在 audio_sec_prev，flags 去掉"已插值"、加上"已重校"。
旧的整份结果另存为 m<N>_measures_before_local.json（每次运行都从它出发，可重复运行）。

用法：python -X utf8 -W ignore align/refine_local.py --movement 2 [--dry]
"""
import argparse
import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "align"
sys.path.insert(0, str(OUT)); sys.path.insert(0, str(ROOT / "tools"))

SR, HOP = 22050, 256                  # 约 86 帧/秒（11.6 毫秒）
FPS = SR / HOP
NOM_RUN = 3                           # 见 regions()
MARG = 3                              # 段两侧各带几个小节作端点
FMIN_MIDI = 24                        # CQT 从 C1 起，84 个半音
HARM = [(0, 1.0), (12, 0.5), (19, 0.35), (24, 0.25), (28, 0.2)]
TAIL = 1.0
W_CQT, W_CHR, W_ON = 0.45, 0.35, 0.20


def regions(rows, extra, pins=()):
    """返回 [(k0, k1, 可疑行集合)]。窗口 [k0, k1)：k0、k1 的起点固定；钉点所在行也作为固定端点把窗口切开。"""
    out, k = [], 0
    n = len(rows)
    while k < n:                                                    # 与 make_checklist 相同的合并规则
        if not rows[k]["flags"] or rows[k]["flags"] == ["已重校"]:
            k += 1; continue
        j = k
        while j + 1 < n and (rows[j + 1]["flags"] or (j + 2 < n and rows[j + 2]["flags"])):
            j += 1
        out.append((k, j)); k = j + 1
    # 连续 NOM_RUN 个以上小节的时长几乎正好等于谱面时长：多半是特征太弱、DTW 按谱面速度走了直线（2026-11-08 用户听出谐谑曲尾声即如此）
    k = 0
    while k < n:
        j = k
        while j < n and abs(rows[j]["audio_dur"] - rows[j]["nominal_dur"]) < 0.03:
            j += 1
        if j - k >= NOM_RUN:
            out.append((k, j - 1))
        k = max(j, k + 1)
    out += [tuple(x) for x in extra]
    wins = sorted((max(0, a - MARG), min(n, b + MARG + 1)) for a, b in out)   # 窗口 [k0, k1)：k0 起点与 k1 起点固定
    merged = []
    for w in wins:
        if merged and w[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], w[1]))
        else:
            merged.append(w)
    sus = set()
    for a_, b_ in out:
        sus.update(range(a_, b_ + 1))
    final = []
    for k0, k1 in merged:
        cuts = [k0] + sorted(p for p in pins if k0 < p < k1) + [k1]
        for x, y in zip(cuts, cuts[1:]):
            if y - x >= 2:
                final.append((x, y, {q for q in sus if x < q < y} or set(range(x + 1, y))))
    return final


def bar_time(rows, k):
    return rows[k]["audio_sec"] if k < len(rows) else rows[-1]["audio_sec"] + rows[-1]["audio_dur"]


def score_template(notes_by_pos, rows, k0, k1, M):
    """谱面模板：窗口内各小节按谱面速度排开，再整体缩放到 M 帧。返回 cqt(84×M)、chroma(12×M)、onset(M)、小节起点帧。"""
    nom = [max(rows[k]["nominal_dur"], 0.05) for k in range(k0, k1)]
    starts = np.concatenate([[0.0], np.cumsum(nom)])
    total = starts[-1]
    sc = (M - 1) / total
    cqt = np.zeros((84, M), np.float32); on = np.zeros(M, np.float32)
    for i, k in enumerate(range(k0, k1)):
        r = rows[k]
        for off, dur, midi in notes_by_pos.get(r["position"], []):
            t0 = starts[i] + min(off / r["q_len"], 1.0) * nom[i]
            t1 = starts[i] + min((off + dur) / r["q_len"], 1.0 + 4.0) * nom[i]
            f0 = int(t0 * sc); f1 = max(f0 + 1, int(t1 * sc))
            tail = int(TAIL * sc * (total / max(total, 1e-6)))
            env = np.ones(f1 - f0 + tail, np.float32); env[f1 - f0:] = np.exp(-np.arange(tail) / max(1, tail / 3))
            g1 = min(M, f0 + len(env))
            if f0 >= M:
                continue
            for dp, w in HARM:
                b = midi + dp - FMIN_MIDI
                if 0 <= b < 84:
                    cqt[b, f0:g1] += w * env[:g1 - f0]
            on[f0] += 1
    return cqt, starts * sc, on


def feats_norm(cqt):
    c = np.log1p(10 * cqt) + 1e-3
    chroma = np.zeros((12, c.shape[1]), np.float32)
    for b in range(84):
        chroma[(b + FMIN_MIDI) % 12] += c[b]
    c /= np.linalg.norm(c, axis=0, keepdims=True); chroma /= np.linalg.norm(chroma, axis=0, keepdims=True)
    return c, chroma


def norm_on(o):
    o = np.convolve(o, np.ones(5) / 5, mode="same")
    return o / (np.percentile(o, 99) + 1e-6)


def cost_matrix(A, Ac, Ao, S, Sc, So):
    C = W_CQT * (1 - A.T @ S) + W_CHR * (1 - Ac.T @ Sc)
    C += W_ON * np.abs(np.clip(Ao, 0, 1)[:, None] - np.clip(So, 0, 1)[None, :])
    return C.astype(np.float32)


try:
    from numba import njit
except ImportError:                                           # numba 没装时用纯 Python（会慢）
    def njit(*a, **k):
        return (lambda f: f) if not a or not callable(a[0]) else a[0]


@njit(cache=True)
def dtw_slope(C):
    """两端固定的 DTW，步型 (1,1)、(1,2)、(2,1)、(1,3)、(3,1)：局部斜率限制在 1/3～3。返回路径（录音帧, 谱面帧）。"""
    N, M = C.shape
    INF = 1e18
    D = np.full((N, M), INF)
    P = np.zeros((N, M), np.int8)
    D[0, 0] = C[0, 0]
    steps_i = (1, 1, 2, 1, 3)
    steps_j = (1, 2, 1, 3, 1)
    for i in range(1, N):
        for j in range(1, M):
            best = INF; arg = -1
            for s in range(5):
                pi = i - steps_i[s]; pj = j - steps_j[s]
                if pi < 0 or pj < 0:
                    continue
                v = D[pi, pj]
                if v >= INF:
                    continue
                # 跳过的中间格也计入代价，免得斜着跳更便宜
                if steps_i[s] == 1 and steps_j[s] > 1:
                    for q in range(1, steps_j[s]):
                        v += C[i, j - q]
                elif steps_j[s] == 1 and steps_i[s] > 1:
                    for q in range(1, steps_i[s]):
                        v += C[i - q, j]
                if v < best:
                    best = v; arg = s
            if arg >= 0:
                D[i, j] = best + C[i, j]; P[i, j] = arg
    path_i = np.zeros(N + M, np.int64); path_j = np.zeros(N + M, np.int64)
    i, j, n = N - 1, M - 1, 0
    while True:
        path_i[n] = i; path_j[n] = j; n += 1
        if i == 0 and j == 0:
            break
        s = P[i, j]
        i -= steps_i[s]; j -= steps_j[s]
        if i < 0 or j < 0:
            break
    return path_i[:n][::-1], path_j[:n][::-1], D[N - 1, M - 1]


def mean_cost_of_mapping(C, a_bounds, s_bounds, lo=0, hi=None):
    """按"小节起点对应关系"（录音帧 ↔ 谱面帧，分段线性）算录音帧 [lo, hi) 的平均代价。"""
    N, M = C.shape
    i = np.arange(max(0, lo), min(N, hi if hi is not None else N))
    j = np.clip(np.interp(i, a_bounds, s_bounds), 0, M - 1).astype(int)
    return float(C[i, j].mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--movement", type=int, required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--show", action="store_true", help="逐小节打印新旧时长")
    a = ap.parse_args()
    mv = a.movement
    import librosa
    import align as A
    from similar_passages import notes as score_notes

    p_cur = OUT / f"m{mv}_measures.json"; p_base = OUT / f"m{mv}_measures_before_local.json"
    res = json.loads(p_cur.read_text(encoding="utf-8"))
    if res.get("local", {}).get("done"):
        res = json.loads(p_base.read_text(encoding="utf-8"))
    elif not a.dry:
        p_base.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = res["measures"]
    man = json.loads((OUT / "manual_regions.json").read_text(encoding="utf-8")) if (OUT / "manual_regions.json").exists() else {}
    extra = man.get(str(mv), {}).get("rows", [])
    pins = {int(k): float(v) for k, v in man.get(str(mv), {}).get("pins", {}).items()}
    for k, t in pins.items():                                   # 钉点：人工核实过的小节起点，直接写入并作为固定端点
        if abs(rows[k]["audio_sec"] - t) > 1e-3:
            rows[k].setdefault("audio_sec_prev", rows[k]["audio_sec"]); rows[k]["audio_sec"] = t
            rows[k]["flags"] = [x for x in rows[k]["flags"] if x != "已插值"] + ["已重校"]
    end_pin = man.get(str(mv), {}).get("end")
    wins = regions(rows, list(extra) + [(max(0, k - 1), k) for k in pins], pins)   # 钉点两侧都要重新对齐
    print(f"第 {mv} 乐章：{len(wins)} 个重校窗口", flush=True)

    d = score_notes(mv)
    notes_by_pos = {}
    for pos, off, dur, midi, pi in d["notes"]:
        notes_by_pos.setdefault(pos, []).append((off, dur, midi))
    f = A.movement_files(mv)
    y, _ = librosa.load(str(f["wav"]), sr=SR, mono=True)

    t_start = time.time(); log = []; n_changed = 0
    for k0, k1, sus in wins:
        T0, T1 = bar_time(rows, k0), bar_time(rows, k1)
        seg = y[int(T0 * SR):int(T1 * SR)]
        if len(seg) < SR:
            continue
        cq = np.abs(librosa.cqt(seg, sr=SR, hop_length=HOP, fmin=librosa.midi_to_hz(FMIN_MIDI), n_bins=84)).astype(np.float32)
        Ao = librosa.onset.onset_strength(y=seg, sr=SR, hop_length=HOP).astype(np.float32)
        N = min(cq.shape[1], len(Ao)); cq, Ao = cq[:, :N], norm_on(Ao[:N])
        Ac_cqt, Ac_chr = feats_norm(cq / (cq.max() + 1e-9) * 20)
        M = N
        s_cqt, s_bounds, So = score_template(notes_by_pos, rows, k0, k1, M)
        Sc, Sch = feats_norm(s_cqt)
        So = norm_on(So)
        C = cost_matrix(Ac_cqt, Ac_chr, Ao, Sc, Sch, So)
        pi, pj, _ = dtw_slope(C)
        # 每个小节起点（谱面帧）在新路径上对应的录音帧
        new_t = {}
        for q, k in enumerate(range(k0, k1)):
            jb = s_bounds[q]
            ii = pi[np.searchsorted(pj, jb)] if jb <= pj[-1] else pi[-1]
            new_t[k] = T0 + ii / FPS
        # 旧、新两种对应关系在同一套代价下比较
        old_a = [(bar_time(rows, k) - T0) * FPS for k in range(k0, k1)] + [N - 1]
        new_a = [(new_t[k] - T0) * FPS for k in range(k0, k1)] + [N - 1]
        sb = list(s_bounds[:-1]) + [M - 1]
        # 只在可疑小节覆盖的录音范围里比较（新旧两种对应关系取并集），免得窗口其余部分的细小差别把整窗否掉
        q0, q1 = min(sus) - k0, max(sus) - k0 + 1
        lo = int(min(old_a[q0], new_a[q0])); hi = int(max(old_a[q1], new_a[q1])) + 1
        c_old = mean_cost_of_mapping(C, old_a, sb, lo, hi); c_new = mean_cost_of_mapping(C, new_a, sb, lo, hi)
        mono = all(new_t[k] < new_t[k + 1] for k in range(k0 + 1, k1 - 1))
        tag = f"第 {rows[k0]['measure']}–{rows[k1 - 1]['measure']} 小节（{T0:.1f}–{T1:.1f} 秒）"
        if a.show:
            for k in range(k0, k1):
                ob = bar_time(rows, k + 1) - bar_time(rows, k)
                nb = (new_t[k + 1] if k + 1 < k1 else T1) - new_t[k]
                print(f"      第 {rows[k]['measure']:>4} 小节  旧 {bar_time(rows, k):7.2f}s 长 {ob:5.2f}  新 {new_t[k]:7.2f}s 长 {nb:5.2f}  谱面 {rows[k]['nominal_dur']:.2f}  {' '.join(rows[k]['flags'])}")
        # 合理性：小节的"实际时长 / 谱面时长"偏离窗口中位数 2 倍以上，算速度突变（插值留下的 1 秒小节就是这样）
        def jumps(times):
            ratio = [(times[q + 1] - times[q]) / max(rows[k0 + q]["nominal_dur"], 0.05) for q in range(len(times) - 1)]
            med = float(np.median(ratio))
            return sum(1 for q, x in enumerate(ratio) if (k0 + q) in sus and (x > 2 * med or x < med / 2))
        old_times = [bar_time(rows, k) for k in range(k0, k1)] + [T1]
        new_times = [new_t[k] for k in range(k0, k1)] + [T1]
        j_old, j_new = jumps(old_times), jumps(new_times)
        better = c_new < c_old - 1e-4 or (c_new < c_old + 0.005 and j_new < j_old)
        if better and mono:
            shifts = []
            for k in range(k0 + 1, k1):
                r = rows[k]
                shifts.append(new_t[k] - r["audio_sec"])
                if abs(new_t[k] - r["audio_sec"]) > 1e-3:
                    r.setdefault("audio_sec_prev", r["audio_sec"])
                    r["audio_sec"] = round(new_t[k], 3)
                    r["flags"] = [x for x in r["flags"] if x != "已插值"] + (["已重校"] if "已重校" not in r["flags"] else [])
                    n_changed += 1
            log.append(f"{tag}：采用（代价 {c_old:.3f} → {c_new:.3f}，速度突变 {j_old} → {j_new} 处，挪动 {min(shifts):+.2f}～{max(shifts):+.2f} 秒）")
        else:
            # 端点被钉点挪过时，原结果的内部小节可能落到端点外面：这种情况下改用新结果（递增时），否则按谱面时长比例重排
            old_in = [bar_time(rows, k) for k in range(k0 + 1, k1)]
            ok_old = all(T0 < x < T1 for x in old_in) and all(x < z for x, z in zip(old_in, old_in[1:]))
            if not ok_old:
                if mono:
                    fill = {k: new_t[k] for k in range(k0 + 1, k1)}
                else:
                    nd = [max(rows[k]["nominal_dur"], 0.05) for k in range(k0, k1)]
                    acc = T0; fill = {}
                    for q, k in enumerate(range(k0 + 1, k1)):
                        acc += nd[q] * (T1 - T0) / sum(nd); fill[k] = acc
                for k, t in fill.items():
                    rows[k].setdefault("audio_sec_prev", rows[k]["audio_sec"]); rows[k]["audio_sec"] = round(t, 3)
                    rows[k]["flags"] = [x for x in rows[k]["flags"] if x != "已插值"] + (["已重校"] if "已重校" not in rows[k]["flags"] else [])
                    n_changed += 1
                log.append(f"{tag}：原结果与钉点冲突，改用{'新结果' if mono else '按谱面比例重排'}")
            else:
                log.append(f"{tag}：保留原结果（代价 {c_old:.3f} → {c_new:.3f}，速度突变 {j_old} → {j_new} 处{'，新结果不递增' if not mono else ''}）")
        print("  " + log[-1], flush=True)
    if end_pin:                                                 # 最后一个小节的结束时刻（尾声后的余响、掌声不算）
        rows[-1]["audio_dur"] = round(end_pin - rows[-1]["audio_sec"], 3)
    for k, r in enumerate(rows):
        if k + 1 < len(rows):
            r["audio_dur"] = round(rows[k + 1]["audio_sec"] - r["audio_sec"], 3)
        r["bpm_quarter"] = round(60 * r["q_len"] / r["audio_dur"], 1) if r["audio_dur"] > 0 else None
    times = [r["audio_sec"] for r in rows]
    ok = all(z > x for x, z in zip(times, times[1:]))
    res["local"] = {"done": True, "method": "局部重校（align/refine_local.py）", "windows": len(wins),
                    "measures_changed": n_changed, "log": log}
    res["checks"]["时间严格递增"] = ok
    print(f"改动 {n_changed} 个小节，用时 {time.time() - t_start:.0f} 秒；时间严格递增：{ok}")
    if not a.dry:
        p_cur.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        A.write_check_wav(f, rows, mv)


if __name__ == "__main__":
    main()
