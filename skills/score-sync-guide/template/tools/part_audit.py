"""声部体检：为判断"重名声部是换乐器、重复还是空谱表"收集证据。只读 mxl。

对每个声部输出：英文名、乐器、移调、有音符的小节数、首末有音符的小节、音域、音符数，
以及与其他声部的关系：有音符的小节是否重叠、重叠小节里内容是否完全相同。

用法（项目 venv 里的 music21 即可）：
    venv\\Scripts\\python.exe -X utf8 tools\\part_audit.py "mahler sympony 6 scherzo.mxl"
输出写到 out/<文件名>_audit.json，并在屏幕上打印摘要。
"""
import json
import pathlib
import sys
import warnings

import music21 as m21

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent


def measure_content(meas) -> tuple:
    """一个小节里所有音符的指纹：(拍内位置, 音高列表, 时值)；休止不计。"""
    out = []
    for n in meas.recurse().notes:
        pitches = tuple(sorted(p.nameWithOctave for p in n.pitches))
        out.append((round(float(n.getOffsetInHierarchy(meas)), 4), pitches, round(float(n.quarterLength), 4)))
    return tuple(sorted(out))


def ranges_of(nums: list[int], gap: int = 4) -> list[list[int]]:
    """把有音符的小节号合并成区间：中间休止不超过 gap 小节算同一段。"""
    rs = []
    for n in nums:
        if rs and n - rs[-1][1] <= gap + 1:
            rs[-1][1] = n
        else:
            rs.append([n, n])
    return rs


def main(fname: str) -> None:
    score = m21.converter.parse(str(ROOT / fname))
    parts = list(score.parts)
    info = []
    for i, p in enumerate(parts):
        content = {}
        pitches = []
        for meas in p.getElementsByClass("Measure"):
            c = measure_content(meas)
            if c:
                content[meas.number] = c
                pitches += [q for n in meas.recurse().notes for q in n.pitches]
        inst = p.getInstrument(returnDefault=False)
        tr = inst.transposition if inst is not None else None
        nums = sorted(content)
        info.append({
            "index": i,
            "name": (p.partName or "").strip(),
            "abbr": (p.partAbbreviation or "").strip(),
            "instrument": inst.instrumentName if inst is not None else None,
            "transposition": tr.directedName if tr is not None else None,
            "measures_with_notes": len(nums),
            "first": nums[0] if nums else None,
            "last": nums[-1] if nums else None,
            "ranges": ranges_of(nums),
            "n_notes": sum(len(c) for c in content.values()),
            "lowest": min(pitches, key=lambda q: q.ps).nameWithOctave if pitches else None,
            "highest": max(pitches, key=lambda q: q.ps).nameWithOctave if pitches else None,
            "_content": content,
        })

    # 两两比较：有音符的小节重叠多少；重叠小节里内容完全相同的占多少
    pairs = []
    for a in range(len(info)):
        for b in range(a + 1, len(info)):
            ca, cb = info[a]["_content"], info[b]["_content"]
            if not ca or not cb:
                continue
            both = set(ca) & set(cb)
            same = sum(1 for k in both if ca[k] == cb[k])
            if both:
                pairs.append({"a": a, "b": b, "overlap": len(both), "identical": same,
                              "a_only": len(set(ca) - set(cb)), "b_only": len(set(cb) - set(ca))})
    for x in info:
        del x["_content"]

    out = ROOT / "out" / (pathlib.Path(fname).stem + "_audit.json")
    out.write_text(json.dumps({"file": fname, "parts": info, "pairs": pairs}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    for x in info:
        print(f'{x["index"]:>2} {x["name"][:28]:<28} {str(x["instrument"])[:18]:<18} 移调={str(x["transposition"]):<14} '
              f'有音小节={x["measures_with_notes"]:>3} 首末={x["first"]}-{x["last"]} 音域={x["lowest"]}-{x["highest"]}')
    print(f"已写 {out}")


if __name__ == "__main__":
    main(sys.argv[1])
