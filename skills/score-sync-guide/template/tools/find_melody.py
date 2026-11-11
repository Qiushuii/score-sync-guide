"""按旋律音程序列找主题的再现（与移调无关），比 similar_passages.py 的和声相似度更能认出"同一条旋律"。
做法：每个声部按起音取最高音，得到一条音高序列；查询旋律取某声部某几小节的同样序列，
比较相邻音程（半音数），允许 --tol 个音程不同（变奏、装饰）。只作线索，须再看谱。
用法：python -X utf8 tools/find_melody.py <乐章> <声部名片段> <起始小节>-<结束小节> [--in 乐章] [--len 8] [--tol 1]
例：python -X utf8 tools/find_melody.py 1 "Violin I" 77-79 --len 9
"""
import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from similar_passages import notes, ROOT  # noqa: E402


def part_names(mv: int) -> list[str]:
    man = {m["id"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["movements"]}
    st = json.loads((ROOT / man[mv]["structure"]).read_text(encoding="utf-8"))
    return [f"{p.get('zh_name') or ''}/{p.get('name')}" for p in st["parts"]]


def lines(d: dict) -> dict:
    """每个声部：[(位置, 拍内偏移, 音高)]，同一时刻取最高音。"""
    by = collections.defaultdict(dict)
    for pos, off, dur, midi, pi in d["notes"]:
        k = (pos, round(off, 3))
        by[pi][k] = max(by[pi].get(k, 0), midi)
    return {pi: sorted((k[0], k[1], v) for k, v in m.items()) for pi, m in by.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("movement", type=int)
    ap.add_argument("part")
    ap.add_argument("span")
    ap.add_argument("--in", dest="target", type=int, default=None)
    ap.add_argument("--len", type=int, default=8, help="比较多少个音（音程数 = len-1）")
    ap.add_argument("--tol", type=int, default=1, help="允许几个音程不同")
    a = ap.parse_args()
    dq = notes(a.movement)
    names_q = part_names(a.movement)
    pi = next(i for i, n in enumerate(names_q) if a.part.lower() in n.lower())
    s, e = a.span.split("-")
    i0, i1 = dq["numbers"].index(s), dq["numbers"].index(e)
    seq = [x for x in lines(dq)[pi] if i0 <= x[0] <= i1][: a.len]
    qi = [y[2] - x[2] for x, y in zip(seq, seq[1:])]
    print(f"查询：{names_q[pi]} 第 {s}–{e} 小节，音程 {qi}")
    tm = a.target or a.movement
    dt = notes(tm)
    names_t = part_names(tm)
    hits = collections.defaultdict(list)
    for p, ln in lines(dt).items():
        for j in range(len(ln) - len(qi)):
            iv = [ln[j + k + 1][2] - ln[j + k][2] for k in range(len(qi))]
            bad = sum(1 for u, v in zip(iv, qi) if u != v)
            if bad <= a.tol:
                hits[ln[j][0]].append((bad, names_t[p], ln[j][2] - seq[0][2]))
    for pos in sorted(hits):
        h = sorted(hits[pos])
        print(f"第 {dt['numbers'][pos]:>5} 小节  " + "；".join(f"{n}（差{b}，移{sh:+d}）" for b, n, sh in h[:4]))


if __name__ == "__main__":
    main()
