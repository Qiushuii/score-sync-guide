"""在谱面数据里搜"格言动机"的候选位置：A 大三和弦（A、C♯、E）在几拍之内转成 A 小三和弦（A、C、E）。

依据（可核查的文献描述）：第六交响曲的核心动机是 A 大三和弦转 A 小三和弦，下面是定音鼓的特征节奏，
第一乐章出现、之后的乐章再现，终乐章结尾只剩 A 小三和弦（见维基百科"Symphony No. 6 (Mahler)"等）。
文献没有给小节号，所以位置由本脚本从谱面数据里找，结果是"候选"，需要逐个对照谱面核实。

做法：把各声部转成实际音高，取每个起音时刻正在响的音级集合（不含无音高打击乐）；
找到"含 A、C♯、E 且不含 C"的时刻，之后 8 拍内出现"含 A、C、E 且不含 C♯"的时刻，记为一处候选；
同时记下那几拍定音鼓有没有在奏、是哪些声部奏的和弦。只读 mxl。

用法：python -X utf8 tools/find_motto.py <乐章号 1-4> [--strict]（严格版：两和弦都是干净三和弦、各持续至少 1 拍）
输出：屏幕列表 + out/motto_candidates_m<N>.json
"""
import json
import pathlib
import sys
import warnings

import music21 as m21

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = {1: "symphony-6-movement-1-mahler.mxl", 2: "symphony-6-movement-2-mahler.mxl",
         3: "mahler sympony 6 scherzo.mxl", 4: "mahler-symphony-no-6-4th-movement.mxl"}
A, CS, C, E = 9, 1, 0, 4
STRICT = "--strict" in sys.argv


def main(mv: int) -> None:
    f = ROOT / "scores_fixed" / FILES[mv]
    if not f.exists():
        f = ROOT / FILES[mv]
    score = m21.converter.parse(str(f)).toSoundingPitch()
    notes = []                                   # (起点, 终点, 音级集合, 声部名)
    timp = []                                    # 定音鼓的起音时刻
    for p in score.parts:
        name = (p.partName or "").strip()
        is_timp = "timp" in name.lower() or "pauk" in name.lower()
        for n in p.flatten().notes:
            ps = getattr(n, "pitches", ())
            if not ps:
                continue
            s = float(n.getOffsetInHierarchy(score)) if False else float(n.offset)
            notes.append((s, s + float(n.quarterLength), {q.pitchClass for q in ps}, name))
            if is_timp:
                timp.append(s)
    times = sorted({s for s, _, _, _ in notes})
    import bisect
    starts = sorted(notes, key=lambda x: x[0])
    S = [x[0] for x in starts]

    def sounding(t):
        k = bisect.bisect_right(S, t)
        pcs, who = set(), set()
        for s, e, pc, name in starts[max(0, k - 4000):k]:
            if s <= t < e:
                pcs |= pc; who.add(name)
        return pcs, who

    meas = score.parts[0].getElementsByClass("Measure")
    m_off = [(float(m.offset), m.number) for m in meas]

    def measure_at(t):
        k = bisect.bisect_right([o for o, _ in m_off], t) - 1
        return m_off[max(k, 0)][1]

    found, last = [], -1e9
    for t in times:
        pcs, who = sounding(t)
        # 严格版：两个和弦都是干净的三和弦（音级集合恰好是 A、C♯、E / A、C、E），且各自至少持续 1 拍
        if STRICT and not (pcs == {A, CS, E} and sounding(t + 1.0)[0] == {A, CS, E}):
            continue
        if {A, CS, E} <= pcs and C not in pcs:
            for t2 in times[bisect.bisect_right(times, t):]:
                if t2 - t > 8:
                    break
                p2, w2 = sounding(t2)
                if STRICT and not (p2 == {A, C, E} and sounding(t2 + 1.0)[0] == {A, C, E}):
                    continue
                if {A, C, E} <= p2 and CS not in p2:
                    if t - last > 8:                     # 相邻候选合并
                        tp = [x for x in timp if t - 4 <= x <= t2 + 2]
                        found.append({"measure_major": measure_at(t), "measure_minor": measure_at(t2),
                                      "offset_q": round(t, 3), "beats_between": round(t2 - t, 3),
                                      "timpani_onsets_nearby": len(tp),
                                      "parts_major": sorted(who)[:8], "parts_minor": sorted(w2)[:8]})
                        last = t
                    break
    out = ROOT / "out" / f"motto_candidates_m{mv}{'_strict' if STRICT else ''}.json"
    out.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"第 {mv} 乐章：{len(found)} 处候选（A 大三和弦 8 拍内转 A 小三和弦）")
    for x in found:
        print(f"  第 {x['measure_major']}→{x['measure_minor']} 小节  间隔 {x['beats_between']} 拍  附近定音鼓起音 {x['timpani_onsets_nearby']} 个  "
              f"大三和弦由：{', '.join(x['parts_major'][:4])}…  小三和弦由：{', '.join(x['parts_minor'][:4])}…")


if __name__ == "__main__":
    main(int(sys.argv[1]))
