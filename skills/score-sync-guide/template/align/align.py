"""乐谱与录音自动对齐：求出每个小节在录音里的起始时间（草稿），并标出低置信度的小节。

做法（与学界常用的"乐谱—录音同步"方法同一思路，简化实现）：
  1. 谱面一侧：用 music21 读谱（修正副本优先），转成实际音高，按谱面速度排成时间轴，
     逐帧算"哪些音级在响"（12 维色度）和"有多少音符起音"（起音包络）。
  2. 录音一侧：用 librosa 从分析用 WAV 算同样两种特征。
  3. 粗对齐：约 5 帧/秒，对两串色度做动态时间规整（DTW），得到整体对应关系。
  4. 精对齐：约 43 帧/秒，只在粗结果附近 ±1.5 秒的带子里，用色度 + 起音再做一次 DTW。
  5. 从对齐路径读出每个小节开头在录音里的时刻；实际速度或匹配代价异常的小节标为低置信度。

输出（align/）：
  m<N>_measures.json  主结果：每小节的谱面位置（四分音符数）、录音时刻、实际速度、置信标记
  m<N>_measures.csv   同上，表格版
  m<N>_check.wav      听检用：录音 + 每小节开头一声轻"嗒"（排练记号处一声高音"叮"）
  m<N>_path.png       诊断图：对齐曲线、实际速度、低置信度小节
  cache/              中间结果，重跑时跳过已算好的步骤（可续跑）

用法（需要装了 librosa、music21 的 Python，即系统 Python）：
    python -X utf8 align/align.py --movement 2
首尾用子序列对齐（谱面整段对到录音的某一段，录音的起点、终点可以浮动），对"原地踏步"加罚分，
避免录音开头的环境声、结尾的掌声把首尾小节对错。
原始录音、原始 mxl 都不改；分析用 WAV 不存在时用 ffmpeg 从 m4a 转一份（单声道 22.05 kHz）。
"""
import argparse
import csv
import datetime
import hashlib
import json
import pathlib
import subprocess
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "align"
CACHE = OUT / "cache"

SR = 22050
HOP = 512                       # 精对齐帧移：512 / 22050 ≈ 23.2 毫秒（约 43 帧/秒）
FPS = SR / HOP
COARSE = 9                      # 粗对齐把 9 帧合成 1 帧（约 4.8 帧/秒）
BAND_SEC = 1.5                  # 精对齐只在粗结果 ±1.5 秒内找
ONSET_W = 0.5                   # 精对齐里起音差异的权重
PAD_HEAD = 0.0                  # 开头不补静音：改为按响度直接找音乐起点（见 music_start），录音从那里开始对齐
HEAD_RISE_DB = 10.0             # 比全曲噪声底（最安静 1%）高出多少 dB、并持续 0.3 秒以上，算音乐开始
HEAD_TOL = 0.4                  # 对齐起点只允许在这个响度起点前后 0.4 秒内浮动
PAD_TAIL = 0.0                  # 结尾不再补静音：改为首尾可浮动的子序列对齐（见 START_WIN/END_WIN）
SKIP_RISE_DB = 6.0              # 跳过录音的代价：比全曲噪声底（最安静 1% 的响度）高不到 6 dB 的帧几乎免费，
SKIP_SPAN_DB = 6.0              # 再高 6 dB 涨到满额 SKIP_MAX。这样环境声、掌声被跳过，弱奏的音乐不会被跳过
SKIP_MAX = 0.6
START_WIN = 30.0                # 谱面第一帧可以对到录音前 30 秒内的任何位置（录音开头的静音、环境声自然被跳过）
END_WIN = 90.0                  # 谱面最后一帧可以对到录音最后 90 秒内的任何位置（尾声后的掌声、噪声自然被跳过）
PAD_COST = 0.5                  # 录音帧对到补的静音时的固定代价：高于正常音乐（中位 0.22、95% 分位 0.37），低于噪声/掌声（>0.6）。
                                # 不能用"与均匀色度的距离"，因为录音色度经对数压缩后本来就偏均匀，静音会太有吸引力。
# 谱面模板（改了要更新 TEMPLATE_VER，缓存才会失效）
TEMPLATE_VER = "h3h5-tail1"
HARMONICS = [(0, 1.0), (7, 0.35), (4, 0.15)]   # 基音、第 3 泛音（高八度+纯五度）、第 5 泛音（两个八度+大三度）
TAIL_SEC, TAIL_TAU = 3.0, 1.0                  # 音符结束后的余响：最长 3 秒，按 1 秒时间常数衰减
STALL_PEN = 0.08                # "原地踏步"（只有录音前进或只有谱面前进）的每步罚分，防止把几个小节压成一瞬


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 输入文件
def movement_files(mv: int) -> dict:
    man = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    m = next(x for x in man["movements"] if x["id"] == mv)
    score = ROOT / "scores_fixed" / m["score"]
    if not score.exists():
        score = ROOT / m["score"]
    wav = ROOT / (m.get("analysis_wav") or f"wav/m{mv}_mono22050.wav")
    return {"score": score, "audio": ROOT / m["audio"], "wav": wav,
            "structure": ROOT / m["structure"], "audio_dur": m["audio_duration_sec"]}


def ensure_wav(f: dict) -> None:
    if f["wav"].exists():
        return
    f["wav"].parent.mkdir(parents=True, exist_ok=True)
    log(f"分析用 WAV 不存在，从 {f['audio'].name} 转换（原文件不动）")
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-n", "-i", str(f["audio"]), "-map", "0:a:0",
                    "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", "-map_metadata", "-1", str(f["wav"])],
                   check=True, capture_output=True)


# ---------------------------------------------------------------- 谱面一侧
def score_side(f: dict, mv: int) -> dict:
    """返回按演奏顺序排列的小节表（位置、谱面小节号、四分音符起点/长度、谱面估计秒数）和谱面特征。"""
    cache = CACHE / f"m{mv}_score.npz"
    meta_c = CACHE / f"m{mv}_score.json"
    sig = hashlib.sha256(f["score"].read_bytes() + TEMPLATE_VER.encode()).hexdigest()[:16]
    if cache.exists() and meta_c.exists() and json.loads(meta_c.read_text(encoding="utf-8")).get("sig") == sig:
        log("谱面特征：用缓存")
        z = np.load(cache)
        return {**json.loads(meta_c.read_text(encoding="utf-8")), "chroma": z["chroma"], "onset": z["onset"]}

    import music21 as m21
    log(f"读谱：{f['score'].relative_to(ROOT)}")
    score = m21.converter.parse(str(f["score"])).toSoundingPitch()
    parts = list(score.parts)
    st = json.loads(f["structure"].read_text(encoding="utf-8"))
    order = st["play_order"]                    # 展开反复后的演奏顺序（小节位置，0 起）
    nominal = st["nominal_start_sec"]           # 按谱面速度估计的每个演奏小节的起始秒数
    meas_lists = [list(p.getElementsByClass("Measure")) for p in parts]

    # 每个演奏小节的长度（四分音符数）：取各声部该小节实际长度的最大值（弱起小节会比拍号短）
    qlen = []
    for pos in order:
        qlen.append(max(float(ml[pos].duration.quarterLength) for ml in meas_lists if pos < len(ml)))
    qstart = np.concatenate([[0.0], np.cumsum(qlen)])[:-1]
    nom = list(nominal)
    last_dur = (nom[-1] - nom[-2]) * qlen[-1] / qlen[-2] if len(nom) > 1 else qlen[-1]
    nom_end = nom + [nom[-1] + last_dur]

    def nominal_sec(k: int, q_in_measure: float) -> float:
        frac = min(max(q_in_measure / qlen[k], 0.0), 1.0) if qlen[k] > 0 else 0.0
        return nom_end[k] + frac * (nom_end[k + 1] - nom_end[k])

    total = nom_end[-1]
    n_frames = int(np.ceil(total * FPS)) + 1
    chroma = np.zeros((12, n_frames), dtype=np.float32)
    onset = np.zeros(n_frames, dtype=np.float32)
    n_notes = 0
    for ml in meas_lists:
        for k, pos in enumerate(order):
            if pos >= len(ml):
                continue
            meas = ml[pos]
            for n in meas.recurse().notes:
                ps = [p for p in getattr(n, "pitches", ()) if p is not None]
                if not ps:                       # 无音高打击乐不参与色度
                    continue
                off = float(n.getOffsetInHierarchy(meas))
                s = nominal_sec(k, off)
                e = nominal_sec(k, off + float(n.quarterLength))
                f0, f1 = int(s * FPS), max(int(s * FPS) + 1, int(e * FPS))
                # 谱面模板：基音 + 第 3、5 泛音（纯五度、大三度音级）；音符结束后按余响衰减延续
                tail = int(TAIL_SEC * FPS)
                env = np.ones(f1 - f0 + tail, dtype=np.float32)
                env[f1 - f0:] = np.exp(-np.arange(tail) / (TAIL_TAU * FPS))
                g1 = min(f0 + len(env), n_frames)
                for p in ps:
                    for dpc, wgt in HARMONICS:
                        chroma[(p.pitchClass + dpc) % 12, f0:g1] += wgt * env[:g1 - f0]
                tie = n.tie.type if n.tie is not None else None
                if tie not in ("stop", "continue"):  # 延音线的后半段不算新起音
                    onset[min(f0, n_frames - 1)] += len(ps)
                n_notes += 1
    log(f"谱面：{len(order)} 个演奏小节，{n_notes} 个音符/和弦，按谱面速度共 {total:.1f} 秒")

    measure_numbers = [meas_lists[0][pos].number for pos in order]
    reh = {}
    for rm in score.recurse().getElementsByClass(m21.expressions.RehearsalMark):
        mm = rm.getContextByClass("Measure")
        if mm is not None:
            reh.setdefault(mm.number, str(rm.content))
    meta = {"sig": sig, "order": order, "measure_numbers": measure_numbers, "qlen": qlen,
            "qstart": qstart.tolist(), "nominal": nom_end, "rehearsal": {str(k): v for k, v in reh.items()}}
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, chroma=chroma, onset=onset)
    meta_c.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {**meta, "chroma": chroma, "onset": onset}


# ---------------------------------------------------------------- 录音一侧
def audio_side(f: dict, mv: int) -> dict:
    cache = CACHE / f"m{mv}_audio.npz"
    if cache.exists():
        log("录音特征：用缓存")
        with np.load(cache) as z:                              # 用 with 读，读完即关闭（Windows 上不关闭就删不掉）
            if "rms" in z:
                return {"chroma": z["chroma"], "onset": z["onset"], "rms": z["rms"], "y_len": int(z["y_len"])}
        cache.unlink()                                         # 旧缓存缺少响度，删掉重算
        return audio_side(f, mv)
    import librosa
    log(f"读录音：{f['wav'].relative_to(ROOT)}")
    y, _ = librosa.load(str(f["wav"]), sr=SR, mono=True)
    log("计算录音色度（CQT），这一步要一两分钟")
    chroma = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP).astype(np.float32)
    onset = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP).astype(np.float32)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0].astype(np.float32)
    n = min(chroma.shape[1], onset.shape[0], rms.shape[0])
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, chroma=chroma[:, :n], onset=onset[:n], rms=rms[:n], y_len=len(y))
    return {"chroma": chroma[:, :n], "onset": onset[:n], "rms": rms[:n], "y_len": len(y)}


# ---------------------------------------------------------------- 特征处理
def norm_chroma(c: np.ndarray) -> np.ndarray:
    c = np.log1p(10.0 * c)                                   # 对数压缩，减小响度差异的影响
    c = c + 1e-3                                             # 全休止的帧变成近似均匀分布，不至于全零
    return (c / np.linalg.norm(c, axis=0, keepdims=True)).astype(np.float32)


def norm_onset(o: np.ndarray, smooth: int = 3) -> np.ndarray:
    if smooth > 1:
        o = np.convolve(o, np.hanning(smooth * 2 + 1), mode="same")
    hi = np.percentile(o, 99) or 1.0
    return np.clip(o / hi, 0, 1).astype(np.float32)


def downsample(x: np.ndarray, k: int) -> np.ndarray:
    n = x.shape[-1] // k * k
    return x[..., :n].reshape(*x.shape[:-1], n // k, k).mean(axis=-1)


# ---------------------------------------------------------------- 对齐
def skip_costs(rms: np.ndarray) -> np.ndarray:
    """每个录音帧"被跳过"（不对到谱面）时的代价，按响度：接近噪声底的几乎免费，明显有声音的代价高。"""
    db = 20 * np.log10(np.convolve(rms, np.ones(5) / 5, mode="same") + 1e-9)
    floor = np.percentile(db, 1)
    return (0.02 + SKIP_MAX * np.clip((db - floor - SKIP_RISE_DB) / SKIP_SPAN_DB, 0, 1)).astype(np.float32)


def music_start(rms: np.ndarray) -> int:
    """按响度找音乐起点（帧）：噪声底取全曲最安静 1% 的响度（不用开头一秒：谐谑曲开头就有声音），
    第一次高出 HEAD_RISE_DB 并持续 0.3 秒以上的位置，再往前退 0.15 秒。"""
    db = 20 * np.log10(np.convolve(rms, np.ones(5) / 5, mode="same") + 1e-9)
    floor = np.percentile(db, 1)
    need = int(0.3 * FPS)
    above = db > floor + HEAD_RISE_DB
    for i in range(len(above) - need):
        if above[i:i + need].all():
            return max(0, i - int(0.15 * FPS))
    return 0


def coarse_dtw(A: np.ndarray, S: np.ndarray, padmask: np.ndarray, skip: np.ndarray, s_lo: float, s_hi: float) -> np.ndarray:
    """粗对齐：与精对齐同一个 DTW（fine_dtw），只是不设带宽限制。
    起点只允许落在录音前 START_WIN 秒、终点只允许落在最后 END_WIN 秒（librosa 的子序列对齐允许起点落在任何位置、
    跳过录音不花代价，会把第二乐章开头两分钟的弱奏整段跳过，所以不用它）。"""
    N, M = A.shape[1], S.shape[1]
    fps_c = FPS / COARSE
    z_a, z_s = np.zeros(N, np.float32), np.zeros(M, np.float32)
    lo, hi = np.zeros(N, np.int64), np.full(N, M - 1, np.int64)
    return fine_dtw(A, S, z_a, z_s, lo, hi, 0.0, STALL_PEN, padmask, PAD_COST, s_hi * fps_c, END_WIN * fps_c, skip,
                    start_lo=int(s_lo * fps_c))


def _coarse_dtw_librosa_unused(A: np.ndarray, S: np.ndarray, padmask: np.ndarray) -> np.ndarray:
    import librosa
    C = 1.0 - (A.T @ S)                                       # 余弦距离（两边都已单位化）
    C[:, padmask] = PAD_COST                                   # 补的静音：固定代价（目前不补，padmask 全为 False）
    # 子序列对齐：谱面整段对到录音的某一段，录音的起点和终点都可以浮动（librosa 要求第一维是"短的那个"，即谱面）。
    # 默认步型依次为 (1,1) 对角、(0,1)、(1,0)；后两种是原地踏步，加罚分
    _, wp = librosa.sequence.dtw(C=np.ascontiguousarray(C.T), subseq=True, backtrack=True,
                                 weights_add=np.array([0.0, STALL_PEN, STALL_PEN]))
    wp = wp[::-1][:, ::-1]                                      # 改成 (录音帧, 谱面帧)，从头到尾
    return wp


def fine_dtw(A, S, oa, os_, lo, hi, w_on, pen, padmask, pad_cost, start_win, end_win, skip, start_lo=0):
    """带约束的 DTW：录音第 i 帧只与谱面 lo[i]..hi[i] 帧比较。返回路径 (i, j) 数组。"""
    from numba import njit

    @njit(cache=False)
    def run(A, S, oa, os_, lo, hi, w_on, pen, padmask, pad_cost, start_lo, start_win, end_win, skipcum):
        N = A.shape[1]
        W = int((hi - lo).max()) + 1
        INF = 1e30
        D = np.full((N, W), INF, dtype=np.float32)            # 单精度，粗对齐时整张表也放得下
        B = np.zeros((N, W), dtype=np.uint8)               # 1 对角、2 上（录音前进）、3 左（谱面前进）
        for i in range(N):
            for w in range(hi[i] - lo[i] + 1):
                j = lo[i] + w
                if padmask[j]:
                    c = pad_cost
                else:
                    c = 1.0
                    for d in range(12):
                        c -= A[d, i] * S[d, j]
                    c += w_on * abs(oa[i] - os_[j])
                if j == 0 and i < start_lo:                  # 起点窗口之前不能开始
                    continue
                if j == 0 and i < start_win:                 # 起点可浮动：从第 i 帧开始 = 跳过前 i 帧，按响度付代价
                    fresh = c + skipcum[i]
                    if i > 0 and lo[i - 1] == 0 and D[i - 1, 0] + pen + c < fresh:
                        D[i, w] = D[i - 1, 0] + pen + c; B[i, w] = 2
                    else:
                        D[i, w] = fresh
                    continue
                best = INF
                bk = 0
                if i > 0:
                    wd = j - 1 - lo[i - 1]                  # (i-1, j-1)
                    if 0 <= wd <= hi[i - 1] - lo[i - 1] and D[i - 1, wd] < best:
                        best = D[i - 1, wd]; bk = 1
                    wu = j - lo[i - 1]                      # (i-1, j)
                    if 0 <= wu <= hi[i - 1] - lo[i - 1] and D[i - 1, wu] + pen < best:
                        best = D[i - 1, wu] + pen; bk = 2
                if w > 0 and D[i, w - 1] + pen < best:      # (i, j-1)
                    best = D[i, w - 1] + pen; bk = 3
                if bk:
                    D[i, w] = best + c
                    B[i, w] = bk
        # 自由终点：在录音最后 end_win 帧里，找谱面最后一帧累计代价最小的那一行
        M1 = S.shape[1] - 1
        i = N - 1
        bestv = INF
        for ii in range(max(0, N - end_win), N):
            v = D[ii, M1 - lo[ii]] + (skipcum[N] - skipcum[ii + 1]) if lo[ii] <= M1 <= hi[ii] else INF   # 终点之后的录音按响度付跳过代价
            if v < bestv:
                bestv = v; i = ii
        j = M1
        out = np.zeros((N + S.shape[1], 2), dtype=np.int64)
        n = 0
        while True:
            out[n, 0] = i; out[n, 1] = j; n += 1
            if j == 0 and B[i, 0] == 0:                 # 回到自由起点
                break
            bk = B[i, j - lo[i]]
            if bk == 1:
                i -= 1; j -= 1
            elif bk == 2:
                i -= 1
            elif bk == 3:
                j -= 1
            else:
                break
        return out[:n][::-1]

    return run(A, S, oa, os_, lo.astype(np.int64), hi.astype(np.int64), float(w_on), float(pen),
               padmask.astype(np.uint8), float(pad_cost), int(start_lo),
               max(int(start_lo) + 1, int(np.ceil(start_win))), int(end_win),   # 起点窗口向上取整、至少一帧（粗分辨率下窗口可能不足一帧）
               np.concatenate([[0.0], np.cumsum(skip, dtype=np.float64)]))


def band_from_coarse(wp: np.ndarray, N: int, M: int) -> tuple[np.ndarray, np.ndarray]:
    """把粗路径放大到精分辨率，得到每个录音帧允许的谱面帧范围。"""
    ai = wp[:, 0] * COARSE + COARSE / 2
    sj = wp[:, 1] * COARSE + COARSE / 2
    order = np.argsort(ai, kind="stable")
    center = np.interp(np.arange(N), ai[order], sj[order])
    R = int(BAND_SEC * FPS)
    lo = np.clip(np.floor(center - R), 0, M - 1).astype(np.int64)
    hi = np.clip(np.ceil(center + R), 0, M - 1).astype(np.int64)
    lo = np.maximum.accumulate(lo)                            # 范围随录音单调前进
    hi = np.minimum.accumulate(hi[::-1])[::-1]
    hi[-1] = M - 1                                             # 最后一行必须能到谱面末帧（起点由子序列对齐自由决定）
    for i in range(1, N):                                      # 相邻两行的范围要连得上
        lo[i] = min(lo[i], hi[i - 1] + 1)
        hi[i] = max(hi[i], lo[i])
    return lo, hi


# ---------------------------------------------------------------- 结果整理
def measures_from_path(path: np.ndarray, sc: dict, cost_frames: np.ndarray, pad0: int) -> list[dict]:
    # 谱面帧 → 录音秒数：同一谱面帧对应多个录音帧时取平均；再保证单调
    j = path[:, 1]; i = path[:, 0]
    M = int(j.max()) + 1
    sums = np.bincount(j, weights=i, minlength=M); cnt = np.bincount(j, minlength=M)
    a_of_s = np.where(cnt > 0, sums / np.maximum(cnt, 1), np.nan)
    good = ~np.isnan(a_of_s)
    a_of_s = np.interp(np.arange(M), np.flatnonzero(good), a_of_s[good])
    a_of_s = np.maximum.accumulate(a_of_s) / FPS
    s_axis = (np.arange(M) - pad0) / FPS                      # 去掉开头补的静音，回到谱面时间

    nom = np.array(sc["nominal"])
    audio = np.interp(nom, s_axis, a_of_s)
    rows = []
    for k in range(len(sc["order"])):
        a0, a1 = audio[k], audio[k + 1]
        n0, n1 = nom[k], nom[k + 1]
        f0, f1 = int(n0 * FPS) + pad0, max(int(n0 * FPS) + 1, int(n1 * FPS)) + pad0
        mnum = sc["measure_numbers"][k]
        rows.append({
            "k": k, "position": sc["order"][k], "measure": mnum,
            "rehearsal": sc["rehearsal"].get(str(mnum)),
            "q_start": round(sc["qstart"][k], 4), "q_len": sc["qlen"][k], "nominal_dur": round(n1 - n0, 3),
            "nominal_sec": round(n0, 3), "audio_sec": round(float(a0), 3),
            "audio_dur": round(float(a1 - a0), 3),
            "bpm_quarter": round(60 * sc["qlen"][k] / (a1 - a0), 1) if a1 > a0 else None,
            "tempo_ratio": round(float((a1 - a0) / (n1 - n0)), 3) if n1 > n0 else None,
            "cost": round(float(np.mean(cost_frames[f0:f1])), 4) if f1 <= len(cost_frames) else None,
            "flags": [],
        })
    # 低置信度：实际速度与前后 ±3 小节的中位数相差超过 1.8 倍；或匹配代价在全曲最高的 5%
    ratios = np.array([r["tempo_ratio"] or np.nan for r in rows])
    costs = np.array([r["cost"] if r["cost"] is not None else np.nan for r in rows])
    c_hi = np.nanpercentile(costs, 95)
    for k, r in enumerate(rows):
        nb = ratios[max(0, k - 3):k + 4]
        med = np.nanmedian(nb)
        if r["tempo_ratio"] and med and not (1 / 1.8 < r["tempo_ratio"] / med < 1.8):
            r["flags"].append("速度突变")
        if r["audio_dur"] is not None and r["audio_dur"] < 0.15:
            r["flags"].append("小节过短")
        if r["cost"] is not None and r["cost"] >= c_hi:
            r["flags"].append("匹配差")
    smooth_flagged(rows)
    return rows


def smooth_flagged(rows: list) -> None:
    """连续几个"速度突变/小节过短"的小节：数据分不清，就在两侧可靠小节之间按谱面时值比例重新分配
    （相当于假定这一小段速度均匀）。仍保留低置信度标记，并注明"已插值"，等听检。"""
    bad = [bool({'速度突变', '小节过短'} & set(r['flags'])) for r in rows]
    n = len(rows)
    # 紧挨着"速度突变"的"匹配差"小节也不可信，一并纳入这一段（向两侧扩展到不再是匹配差为止）
    changed = True
    while changed:
        changed = False
        for q in range(n):
            if not bad[q] and '匹配差' in rows[q]['flags'] and ((q > 0 and bad[q - 1]) or (q + 1 < n and bad[q + 1])):
                bad[q] = True; changed = True
    k = 0
    while k < n:
        if not bad[k]:
            k += 1; continue
        j = k
        while j < n and bad[j]:
            j += 1
        a, b = k - 1, j                       # 左右的可靠小节（b 可能越界）
        if a >= 0 and b < n:
            t0, t1 = rows[a]['audio_sec'] + rows[a]['audio_dur'], rows[b]['audio_sec']
            if t1 > t0:
                w = [rows[q]['nominal_dur'] for q in range(k, j)]
                acc = t0
                for q, wq in zip(range(k, j), w):
                    d = (t1 - t0) * wq / sum(w)
                    rows[q]['audio_sec'], rows[q]['audio_dur'] = round(acc, 3), round(d, 3)
                    rows[q]['bpm_quarter'] = round(60 * rows[q]['q_len'] / d, 1)
                    rows[q]['flags'].append('已插值'); acc += d
        k = j


SNAP_ENABLED = False                   # 2026-11-07 试验后关闭：物理起点比对齐时刻还早 60–77 毫秒，吸附会让提示更早，
                                       # 与用户"提示音略早"的听感相反。听感上的"早"来自感知起音（P-center）晚于物理起点，
                                       # 改由播放器的全局延后量按红点测试调整。代码保留备用。
SNAP_BEFORE, SNAP_AFTER = 0.10, 0.06   # 吸附窗口：在对齐时刻之前 100 毫秒、之后 60 毫秒内找起音
SNAP_HOP = 128                         # 精细起音检测的帧移（约 5.8 毫秒）


def snap_to_onsets(rows: list, sc: dict, wav: pathlib.Path) -> dict:
    """把小节开头吸附到录音里的真实起音（物理上声音开始上升的那一刻）。
    DTW 的时间分辨率约 23 毫秒，比较的是一段时间的音色而不是"音从哪一刻开始"，会差几十毫秒。
    这里在对齐时刻附近（-100～+60 毫秒）用每约 6 毫秒一格的起音检测，起音后回溯到能量开始上升处，
    再把小节线挪过去。只在两种条件同时满足时才吸附：
      1) 谱面上该小节第一拍确实有新起的音（不是上一小节延续来的长音、也不是休止）；
      2) 附近确实有清楚的起音。
    低置信度、插值过的小节不吸附。吸附后重算各小节时长和速度。"""
    import librosa
    import soundfile as sf
    y, sr = sf.read(str(wav), dtype="float32")
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=SNAP_HOP)
    ons = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=SNAP_HOP, units="frames")
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=SNAP_HOP)[0]
    ons_bt = librosa.onset.onset_backtrack(ons, rms)              # 回溯到起音前的能量低点
    strength = env[ons]
    thr = np.percentile(strength, 40)                             # 只认较清楚的起音
    t_on = ons_bt[strength >= thr] * SNAP_HOP / sr
    score_on = sc["onset"]                                        # 谱面起音计数（按谱面速度的帧）
    moved = []
    for k, r in enumerate(rows):
        if r["flags"]:
            continue
        f0 = int(r["nominal_sec"] * FPS)
        if score_on[max(0, f0 - 1):f0 + 2].sum() < 1:              # 第一拍没有新起的音：不动
            continue
        t = r["audio_sec"]
        c = t_on[(t_on >= t - SNAP_BEFORE) & (t_on <= t + SNAP_AFTER)]
        if not len(c):
            continue
        new = float(c[np.argmin(np.abs(c - t))])
        lo = rows[k - 1]["audio_sec"] + 0.05 if k > 0 else 0.0     # 不越过前后小节
        hi = rows[k + 1]["audio_sec"] - 0.05 if k + 1 < len(rows) else 1e9
        if lo < new < hi:
            moved.append(new - t)
            r["audio_sec"] = round(new, 3); r["snapped_ms"] = round(1000 * (new - t))
    for k, r in enumerate(rows):                                  # 重算时长与速度
        if k + 1 < len(rows):
            r["audio_dur"] = round(rows[k + 1]["audio_sec"] - r["audio_sec"], 3)
        if r["audio_dur"] and r["audio_dur"] > 0:
            r["bpm_quarter"] = round(60 * r["q_len"] / r["audio_dur"], 1)
    mv = np.array(moved) * 1000 if moved else np.zeros(1)
    return {"snapped": len(moved), "eligible_total": len(rows), "median_shift_ms": round(float(np.median(mv)), 1),
            "iqr_ms": [round(float(np.percentile(mv, 25)), 1), round(float(np.percentile(mv, 75)), 1)]}


def write_check_wav(f: dict, rows: list, mv: int) -> pathlib.Path:
    """听检音频：录音 + 每小节开头一声"嗒"（排练记号处一声高音"叮"）。只在本机生成，不外传。"""
    import soundfile as sf
    y, sr = sf.read(str(f["wav"]), dtype="float32")
    y = y * 0.7
    def click(freq, dur=0.035, amp=0.35):
        t = np.arange(int(dur * sr)) / sr
        return (amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * 90)).astype(np.float32)
    # 嗒：短而尖、偏响，避免被强奏起音盖住；叮：更高更长
    tick, ding = click(2600, dur=0.03, amp=0.55), click(3800, dur=0.09, amp=0.6)
    for r in rows:
        c = ding if r["rehearsal"] else tick
        i = int(r["audio_sec"] * sr)
        if 0 <= i < len(y) - len(c):
            y[i:i + len(c)] += c
    out = OUT / f"m{mv}_check.wav"
    sf.write(str(out), np.clip(y, -1, 1), sr, subtype="PCM_16")
    return out


def write_plot(rows: list, mv: int, path_sec: np.ndarray) -> pathlib.Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    ax[0].plot(path_sec[:, 1], path_sec[:, 0], lw=0.8, color="#3d5a80")
    ax[0].set_ylabel("录音时间（秒）"); ax[0].set_title(f"第 {mv} 乐章：谱面（按谱面速度）与录音的对应")
    x = [r["nominal_sec"] for r in rows]; b = [r["bpm_quarter"] or np.nan for r in rows]
    ax[1].plot(x, b, lw=1, color="#3d5a80")
    for r in rows:
        if r["flags"]:
            ax[1].axvline(r["nominal_sec"], color="#d1342b", alpha=0.35, lw=1)
        if r["rehearsal"]:
            ax[1].text(r["nominal_sec"], np.nanmax(b) * 1.02, r["rehearsal"], fontsize=7, ha="center")
    ax[1].set_ylabel("实际速度（四分音符/分）"); ax[1].set_xlabel("谱面时间（秒，按谱面速度）")
    fig.tight_layout()
    out = OUT / f"m{mv}_path.png"
    fig.savefig(out, dpi=110); plt.close(fig)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--movement", type=int, required=True)
    args = ap.parse_args()
    mv = args.movement
    t0 = time.time()
    f = movement_files(mv)
    ensure_wav(f)
    sc = score_side(f, mv)
    au = audio_side(f, mv)

    A = norm_chroma(au["chroma"]); S = norm_chroma(sc["chroma"])
    oa = norm_onset(au["onset"], 2); os_ = norm_onset(sc["onset"], 2)
    # 谱面首尾补"静音"帧（色度均匀、无起音）
    pad0, pad1 = int(PAD_HEAD * FPS), int(PAD_TAIL * FPS)
    flat = np.full((12, 1), 1 / np.sqrt(12), dtype=np.float32)
    S = np.concatenate([np.repeat(flat, pad0, 1), S, np.repeat(flat, pad1, 1)], axis=1)
    os_ = np.concatenate([np.zeros(pad0, np.float32), os_, np.zeros(pad1, np.float32)])
    # 开头不补静音（PAD_HEAD=0），改为按响度找到音乐起点后裁掉之前的录音；
    # 结尾补的静音用固定代价（结尾渐弱的和弦色度也偏均匀，若按相似度会被静音吞掉）
    padmask = np.zeros(S.shape[1], dtype=bool); padmask[S.shape[1] - pad1:] = True
    a0 = 0
    # 起点：有人工核对过的锚点（align/anchors.json）就用锚点 ±0.1 秒，否则按响度判断 ±HEAD_TOL 秒
    anc_p = OUT / "anchors.json"
    anc = json.loads(anc_p.read_text(encoding="utf-8")).get(str(mv)) if anc_p.exists() else None
    if anc:
        st0, tol = float(anc["audio_sec"]), float(anc.get("tol", 0.1))
        log(f"起点锚点：第 {anc['measure']} 小节在录音第 {st0:.2f} 秒（align/anchors.json）")
    else:
        st0, tol = music_start(au["rms"]) / FPS, HEAD_TOL
        log(f"无锚点，按响度判断音乐约从录音第 {st0:.2f} 秒开始")
    s_lo, s_hi = max(0.0, st0 - tol), st0 + tol
    A, oa = A[:, a0:], oa[a0:]
    skip = skip_costs(au["rms"][:A.shape[1]])
    N, M = A.shape[1], S.shape[1]
    log(f"特征帧数：录音 {N}，谱面 {M}（{FPS:.1f} 帧/秒）")

    cp = CACHE / f"m{mv}_coarse.npy"
    if cp.exists():
        wp = np.load(cp); log("粗对齐：用缓存")
    else:
        log("粗对齐（约 5 帧/秒）")
        Ac, Sc = downsample(A, COARSE), downsample(S, COARSE)
        Ac /= np.linalg.norm(Ac, axis=0, keepdims=True); Sc /= np.linalg.norm(Sc, axis=0, keepdims=True)
        pm_c = downsample(padmask.astype(np.float32), COARSE) > 0.5
        wp = coarse_dtw(Ac, Sc, pm_c, downsample(skip, COARSE), s_lo, s_hi); np.save(cp, wp)

    log(f"精对齐（{FPS:.0f} 帧/秒，带宽 ±{BAND_SEC} 秒）")
    lo, hi = band_from_coarse(wp, N, M)
    path = fine_dtw(A, S, oa, os_, lo, hi, ONSET_W, STALL_PEN, padmask, PAD_COST, s_hi * FPS, END_WIN * FPS, skip,
                    start_lo=int(s_lo * FPS))
    cost_s = np.zeros(M, dtype=np.float32); cnt = np.zeros(M)
    c = 1.0 - np.einsum("di,di->i", A[:, path[:, 0]], S[:, path[:, 1]])
    np.add.at(cost_s, path[:, 1], c); np.add.at(cnt, path[:, 1], 1)
    cost_s = cost_s / np.maximum(cnt, 1)

    path = path + [a0, 0]                                       # 录音帧号加回裁掉的开头
    rows = measures_from_path(path, sc, cost_s, pad0)
    snap = snap_to_onsets(rows, sc, f["wav"]) if SNAP_ENABLED else {"snapped": 0, "note": "已关闭（见 SNAP_ENABLED 注释）"}
    if SNAP_ENABLED:
        log(f"小节线吸附到真实起音：{snap['snapped']}/{snap['eligible_total']} 个小节，挪动中位数 {snap['median_shift_ms']} 毫秒（四分位 {snap['iqr_ms']}）")

    # 自动检查
    times = [r["audio_sec"] for r in rows]
    checks = {
        "小节数与演奏顺序一致": len(rows) == len(sc["order"]),
        "时间严格递增": all(b > a for a, b in zip(times, times[1:])),
        "首小节在录音范围内": 0 <= times[0] < f["audio_dur"],
        "末小节在录音范围内": times[-1] < f["audio_dur"],
    }
    flagged = [r for r in rows if r["flags"]]
    OUT.mkdir(exist_ok=True)
    result = {
        "movement": mv, "score": str(f["score"].relative_to(ROOT)), "audio": str(f["audio"].relative_to(ROOT)),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "method": f"色度(CQT, 对数压缩) + 起音包络；粗 DTW {FPS / COARSE:.1f} 帧/秒 → 带约束精 DTW {FPS:.1f} 帧/秒（±{BAND_SEC} 秒），起音权重 {ONSET_W}；开头按响度定起点（全曲噪声底 +{HEAD_RISE_DB:.0f} dB 持续 0.3 秒），对齐起点在其 ±{HEAD_TOL} 秒内浮动；结尾为子序列对齐（终点在最后 {END_WIN:.0f} 秒内浮动，跳过的录音按响度付代价：噪声底以上 {SKIP_RISE_DB:.0f}–{SKIP_RISE_DB + SKIP_SPAN_DB:.0f} dB 从近乎免费升到 {SKIP_MAX}），原地踏步罚分 {STALL_PEN}，静音固定代价 {PAD_COST}",
        "status": "草稿：自动对齐结果，未经人工核对",
        "checks": checks, "n_flagged": len(flagged), "snap": snap,
        "note": "audio_sec 是小节开头在录音里的秒数；q_start 是该小节开头的谱面位置（四分音符数，从演奏顺序第一个小节算起），"
                "播放器用 q_start→audio_sec 把 Verovio 时间表里每个音符的位置换成录音时间。转实际音高时用的是 mxl 里的移调信息，其中个别单簧管的移调值有误（见 CLAUDE.md 第 5 节），对色度影响很小。",
        "measures": rows,
    }
    (OUT / f"m{mv}_measures.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(OUT / f"m{mv}_measures.csv", "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["序号", "谱面小节号", "排练记号", "录音时刻(秒)", "录音时刻(分:秒)", "时长(秒)", "实际速度(四分音符/分)", "低置信度"])
        for r in rows:
            m, s = divmod(r["audio_sec"], 60)
            w.writerow([r["k"], r["measure"], r["rehearsal"] or "", r["audio_sec"], f"{int(m)}:{s:05.2f}",
                        r["audio_dur"], r["bpm_quarter"], "、".join(r["flags"])])
    wav = write_check_wav(f, rows, mv)
    png = write_plot(rows, mv, (path - [0, pad0]) / FPS)
    log(f"完成，用时 {time.time() - t0:.0f} 秒。自动检查：{checks}；低置信度小节 {len(flagged)} 个")
    log(f"输出：{(OUT / f'm{mv}_measures.json').relative_to(ROOT)}、.csv、{wav.relative_to(ROOT)}、{png.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
