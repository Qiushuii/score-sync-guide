"""找谱面里与指定段落相似的地方（用来核实"某主题/引子在第几小节再现"一类说法，结果只作线索，须再看谱）。
特征：每小节按实际音高、按时值加权的 12 维音级分布（色度），外加最低音的音级；连续 L 小节拼成一个窗口，
与查询段落的窗口做余弦相似度，并允许整体移调（12 个移调取最好）。
音符表用 music21 读一次后缓存到 out/notes_m<N>.json。
用法：python -X utf8 tools/similar_passages.py <乐章> <起始小节>-<结束小节> [--in 乐章] [--top 10] [--no-transpose]
"""
import argparse
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent


def notes(mv: int) -> dict:
    cache = ROOT / "out" / f"notes_m{mv}.json"
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
    numbers, out = None, []
    for pi, p in enumerate(sc.parts):
        ms = list(p.getElementsByClass("Measure"))
        if numbers is None:
            numbers = [str(m.number) + (m.numberSuffix or "") for m in ms]
        for pos, m in enumerate(ms):
            for n in m.recurse().notes:
                for x in getattr(n, "pitches", ()):
                    out.append([pos, float(n.getOffsetInHierarchy(m)), float(n.duration.quarterLength), int(round(x.ps)), pi])
    d = {"numbers": numbers, "notes": out, "source": str(src.relative_to(ROOT))}
    cache.write_text(json.dumps(d), encoding="utf-8")
    return d


def bar_features(d: dict) -> np.ndarray:
    nb = len(d["numbers"])
    chroma = np.zeros((nb, 12))
    low = np.full(nb, 999)
    for pos, off, dur, midi, _ in d["notes"]:
        chroma[pos, midi % 12] += max(dur, 0.05)
        low[pos] = min(low[pos], midi)
    bass = np.zeros((nb, 12))
    for i, m in enumerate(low):
        if m < 999:
            bass[i, m % 12] = 1.0
    chroma /= np.maximum(np.linalg.norm(chroma, axis=1, keepdims=True), 1e-9)
    return np.hstack([chroma, 0.5 * bass])


def window(F: np.ndarray, i: int, L: int, shift: int = 0) -> np.ndarray:
    W = F[i:i + L].copy()
    if shift:
        W[:, :12] = np.roll(W[:, :12], shift, axis=1)
        W[:, 12:] = np.roll(W[:, 12:], shift, axis=1)
    v = W.ravel()
    return v / max(np.linalg.norm(v), 1e-9)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("movement", type=int)
    ap.add_argument("span", help="起始小节-结束小节（谱面小节号）")
    ap.add_argument("--in", dest="target", type=int, default=None, help="在哪个乐章里找（默认同一乐章）")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--no-transpose", action="store_true")
    a = ap.parse_args()
    dq = notes(a.movement)
    dt = notes(a.target or a.movement)
    s, e = a.span.split("-")
    i0, i1 = dq["numbers"].index(s), dq["numbers"].index(e)
    L = i1 - i0 + 1
    Fq, Ft = bar_features(dq), bar_features(dt)
    q = window(Fq, i0, L)
    res = []
    for j in range(len(dt["numbers"]) - L + 1):
        best = max(((float(q @ window(Ft, j, L, sh)), sh) for sh in ([0] if a.no_transpose else range(12))))
        res.append((best[0], j, best[1]))
    res.sort(reverse=True)
    picked = []
    for sc, j, sh in res:
        if any(abs(j - k) < max(2, L // 2) for _, k, _ in picked):
            continue
        same = (a.target in (None, a.movement)) and abs(j - i0) < max(2, L // 2)
        picked.append((sc, j, sh))
        print(f"{'（自身）' if same else '':6} 第 {dt['numbers'][j]}–{dt['numbers'][j + L - 1]} 小节  相似度 {sc:.3f}"
              + (f"  移调 {sh:+d} 半音" if sh else ""))
        if len(picked) >= a.top:
            break


if __name__ == "__main__":
    main()
