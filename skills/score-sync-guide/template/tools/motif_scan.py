"""动机扫描：给定一个查询旋律（乐章、声部、小节范围），在指定乐章里找出所有出现位置，
按"音程序列 + 节奏比例"比较，允许整体移调、八度换位（可选）和少量音程不同。结果按小节合并成段，列出奏出的声部（英文名，
可直接写进 motifs.json 的 parts.name）。只作线索，写进 motifs.json 前要看结果是否合理。

用法：python -X utf8 tools/motif_scan.py <乐章> <声部名片段> <起-止小节> [--len 8] [--tol 1] [--in 1,2]
          [--octave] [--rhythm 0.3] [--skip 起-止]（--skip：查询所在的乐章里要排除的小节范围）
"""
import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from similar_passages import notes, ROOT  # noqa: E402


def parts_en(mv: int) -> list[str]:
    man = {m["id"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["movements"]}
    st = json.loads((ROOT / man[mv]["structure"]).read_text(encoding="utf-8"))
    return [(p.get("name") or "").replace("&amp;", "&") for p in st["parts"]], [p.get("zh_name") or "" for p in st["parts"]]


def lines(d: dict) -> dict:
    """每个声部：[(全局拍位置, 小节位置, 音高, 时值)]，同一时刻取最高音；全局拍位置按各小节实际长度累加。"""
    mlen = collections.defaultdict(float)
    for pos, off, dur, midi, pi in d["notes"]:
        mlen[pos] = max(mlen[pos], off + dur)
    start, acc = {}, 0.0
    for pos in range(len(d["numbers"])):
        start[pos] = acc
        acc += mlen.get(pos) or 4.0
    by = collections.defaultdict(dict)
    for pos, off, dur, midi, pi in d["notes"]:
        k = round(start[pos] + off, 3)
        if k not in by[pi] or midi > by[pi][k][1]:
            by[pi][k] = (pos, midi, dur)
    return {pi: sorted((k, v[0], v[1], v[2]) for k, v in m.items()) for pi, m in by.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("movement", type=int)
    ap.add_argument("part")
    ap.add_argument("span")
    ap.add_argument("--len", type=int, default=8)
    ap.add_argument("--tol", type=int, default=1)
    ap.add_argument("--in", dest="targets", default=None)
    ap.add_argument("--octave", action="store_true", help="音程按八度等价比较（方向须相同）")
    ap.add_argument("--rhythm", type=float, default=None, help="节奏容差（相对起音间隔比例），不给就不比节奏")
    ap.add_argument("--skip", default=None)
    ap.add_argument("--json", default=None, help="把结果另存为 JSON")
    a = ap.parse_args()

    dq = notes(a.movement)
    en_q, zh_q = parts_en(a.movement)
    s, e = a.span.split("-")
    i0, i1 = dq["numbers"].index(s), dq["numbers"].index(e)
    LQ = lines(dq)
    exact = [i for i, n in enumerate(en_q) if a.part.lower() == n.lower()]
    cand = exact or [i for i, n in enumerate(en_q) if a.part.lower() in n.lower()]
    # 同名的行可能有好几条（如终乐章 6 行都叫 Sechs Trompeten）：取查询范围内音最多的那一行
    pi = max(cand, key=lambda i: sum(1 for x in LQ.get(i, []) if i0 <= x[1] <= i1))
    seq = [x for x in LQ.get(pi, []) if i0 <= x[1] <= i1][: a.len]
    if len(seq) < 3:
        sys.exit(f"{en_q[pi]} 第 {s}–{e} 小节音太少（{len(seq)} 个）")
    qi = [y[2] - x[2] for x, y in zip(seq, seq[1:])]
    qd = [y[0] - x[0] for x, y in zip(seq, seq[1:])]
    print(f"查询：第 {a.movement} 乐章 {en_q[pi]}（{zh_q[pi]}）第 {s}–{e} 小节，{len(seq)} 个音，音程 {qi}，起音间隔 {qd}")

    def same(u, v):
        if u == v:
            return True
        return a.octave and u % 12 == v % 12 and (u > 0) == (v > 0) and u != 0 and v != 0

    targets = [int(x) for x in a.targets.split(",")] if a.targets else [a.movement]
    out = {}
    for tm in targets:
        d = notes(tm)
        en, zh = parts_en(tm)
        N = d["numbers"]
        skip = None
        if a.skip and tm == a.movement:
            ss, se = a.skip.split("-")
            skip = (N.index(ss), N.index(se))
        hits = []
        for p, ln in lines(d).items():
            for j in range(len(ln) - len(qi)):
                w = ln[j:j + len(qi) + 1]
                iv = [w[k + 1][2] - w[k][2] for k in range(len(qi))]
                bad = sum(1 for u, v in zip(iv, qi) if not same(u, v))
                if bad > a.tol:
                    continue
                if a.rhythm is not None:
                    dd = [w[k + 1][0] - w[k][0] for k in range(len(qi))]
                    ok = any(all(abs(x - y * f) <= a.rhythm * y * f + 1e-6 for x, y in zip(dd, qd)) for f in (0.5, 1, 2))
                    if not ok:
                        continue
                if skip and skip[0] <= w[0][1] <= skip[1]:
                    continue
                hits.append((w[0][1], w[-1][1], p, bad, w[0][2] - seq[0][2]))
        # 按小节合并：开始小节相差 ≤2 的命中归为同一处
        hits.sort()
        groups = []
        for h in hits:
            if groups and h[0] - groups[-1]["end"] <= 2:
                g = groups[-1]
                g["end"] = max(g["end"], h[1]); g["parts"].setdefault(h[2], [h[0], h[1], h[3], h[4]])
                pp = g["parts"][h[2]]; pp[0] = min(pp[0], h[0]); pp[1] = max(pp[1], h[1]); pp[2] = min(pp[2], h[3])
            else:
                groups.append({"start": h[0], "end": h[1], "parts": {h[2]: [h[0], h[1], h[3], h[4]]}})
        print(f"== 第 {tm} 乐章：{len(groups)} 处")
        res = []
        for g in groups:
            desc = "；".join(f"{zh[p] or en[p]}（{N[v[0]]}–{N[v[1]]}，差{v[2]}，移{v[3]:+d}）" for p, v in sorted(g["parts"].items()))
            print(f"  第 {N[g['start']]}–{N[g['end']]} 小节：{desc}")
            res.append({"from": N[g["start"]], "to": N[g["end"]],
                        "parts": [{"name": en[p], "zh": zh[p], "from": N[v[0]], "to": N[v[1]], "mismatch": v[2], "shift": v[3]}
                                  for p, v in sorted(g["parts"].items())]})
        out[tm] = res
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
