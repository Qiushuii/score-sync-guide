"""把 scores_fixed/dynamics_pdf.json（对照原谱查到的力度记号）转成 corrections.json 里 type=dynamic 的条目。
占位字的位置取自 out/other_dynamics.json（声部 id、小节、文档顺序）。同一声部同一小节有几处时，按从后往前的顺序登记，
免得前面的替换让后面的序号错位。重复运行会先删掉旧的 dynamic 条目再重写。
用法：python -X utf8 tools/apply_dynamics_pdf.py   然后  python -X utf8 tools/fix_mxl.py
"""
import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    D = json.loads((ROOT / "scores_fixed" / "dynamics_pdf.json").read_text(encoding="utf-8"))["entries"]
    OD = json.loads((ROOT / "out" / "other_dynamics.json").read_text(encoding="utf-8"))
    C = json.loads((ROOT / "scores_fixed" / "corrections.json").read_text(encoding="utf-8"))
    C["corrections"] = [c for c in C["corrections"] if c.get("type") != "dynamic"]
    new, missing, used = [], [], set()
    for e in D:
        rows = [r for r in OD[str(e["mv"])] if int(r["measure"]) == e["m"]]
        by = collections.defaultdict(list)
        for r in rows:
            by[(r["part"], r["part_id"])].append(r)
        for name in e["parts"]:
            keys = [k for k in by if k[0] == name]
            if not keys:
                missing.append(f"第 {e['mv']} 乐章第 {e['m']} 小节 {name}")
                continue
            for k in keys:
                n = len(by[k])
                for nth in reversed(range(n)):
                    if (e["mv"], e["m"], k[1], nth) in used:
                        continue
                    used.add((e["mv"], e["m"], k[1], nth))
                    new.append({"id": f"m{e['mv']}-d{e['m']:03d}-{k[1]}-{nth}", "type": "dynamic", "movement": e["mv"],
                                "part": k[1], "part_name": k[0], "measure": e["m"], "nth": nth, "count": nth + 1,
                                "dyn": e["dyn"], "words": e.get("words"),
                                "evidence": f"原谱 PDF 第 {e['page']} 页：{e['dyn']}{' ' + e['words'] if e.get('words') else ''}",
                                "note": "力度占位字 other-dynamics，对照原谱补全（用户 2026-11-07 要求对照 PDF 校对）", "date": "2026-11-07"})
    C["corrections"] += new
    (ROOT / "scores_fixed" / "corrections.json").write_text(json.dumps(C, ensure_ascii=False, indent=1), encoding="utf-8")
    left = {mv: len(v) for mv, v in OD.items()}
    for c in new:
        left[str(c["movement"])] -= 1
    print(f"登记 {len(new)} 处力度修正；各乐章剩余占位字：{left}")
    if missing:
        print("找不到的声部：", "；".join(missing))


if __name__ == "__main__":
    main()
