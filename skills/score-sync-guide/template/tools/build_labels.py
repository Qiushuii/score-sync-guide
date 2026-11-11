"""生成 labels.json：第三、四乐章每条谱表的中文显示名、显示方式和依据。只读 mxl。

判断依据（2026-11-07）：
1. 原谱 PDF 首页编制（谐谑曲第 100 页、终乐章第 149 页）。
2. tools/part_audit.py 的体检结果（out/*_audit.json）：每条谱表有音符的小节、两两重叠情况。
3. 谱面上的乐手标记文字（如 "1.2.3." "a 2" "4. Klar. nimmt D-Klar."）。

结论：这两份 mxl 的木管、铜管谱表是"几位乐手共用一行"（与原谱写法一致），
不是一位乐手一行。所以共用行只写乐器名，不写编号；
实际是哪几位乐手，用行里的乐手标记（marks）在播放时动态显示。

用法：venv\\Scripts\\python.exe -X utf8 tools\\build_labels.py
"""
import json
import pathlib
import re
import warnings

import music21 as m21

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent

# 乐手标记：含 "1." 这类编号，或 "a 2" 这类齐奏人数
MARK = re.compile(r"(^|[\s(])\d\.|\ba\s?\d\b|\bSolo\b|nimmt|nehmen|muta|übernimmt|tacet", re.I)

# 每个乐章：序号 → (中文显示名, 分组, 动作, 依据)。动作：show 显示 / hide 隐藏 / merge:<序号> 与该行合并
# 没写到的序号沿用 parts.csv 的自动中文名。
SCHERZO = {
    1: ("长笛", "木管", "show", "原谱'四支长笛'写在两行；本行标记有 1.2./3./4.，是共用行"),
    2: ("长笛", "木管", "show", "共用行，标记有 3.4./2.3. 等"),
    3: ("双簧管", "木管", "show", "原谱'四支双簧管'写在两行；共用行"),
    4: ("双簧管", "木管", "show", "共用行"),
    7: ("单簧管（降B调）", "木管", "show", "原谱'三支降B调单簧管'；本行标记有 1./2./3.，是共用行"),
    8: ("单簧管（A调）", "木管", "show", "换 A 调单簧管用的行（标记 in A / muta in B），乐手 1–3 都用过"),
    9: ("单簧管（降B调）", "木管", "show", "共用行，标记 2.3."),
    10: ("单簧管（降B调）", "木管", "show", "只有第 328–333 小节有音符，无乐手标记"),
    11: ("低音单簧管", "木管", "show", "原谱只有一支低音单簧管"),
    12: ("低音单簧管", "木管", "merge:11", "A 调低音单簧管的行，与第 11 行没有任何同时发声的小节（标记 in A / muta in B），是同一人换乐器"),
    13: ("巴松", "木管", "show", "原谱'三支巴松'写在一行；共用行"),
    14: ("巴松", "木管", "show", "共用行"),
    16: ("圆号", "铜管", "show", "名为'1 & 2'，但标记有 1.3./5./7. 等，是共用行"),
    17: ("圆号", "铜管", "show", "名为'5 & 6'，但标记有 2.4.6.8. 等，是共用行"),
    18: ("圆号", "铜管", "show", "名为'3 & 4'，但标记有 5.6./2.4. 等，是共用行"),
    19: ("圆号", "铜管", "show", "名为'7 & 8'，但标记有 6.8./5.7. 等，是共用行"),
    20: ("小号（降B调）", "铜管", "show", "原谱'四支小号'；标记有 1./1.2./3./4.，是共用行"),
    21: ("小号（F调）", "铜管", "show", "与第 20 行没有同时发声的小节，标记有 1./1.2.，是换 F 调小号用的行"),
    22: ("小号（降B调）", "铜管", "show", "共用行，标记 2./3./3.4. 等"),
    23: ("小号（F调）", "铜管", "show", "与第 22 行没有同时发声的小节，标记 3./3.4."),
    24: ("长号", "铜管", "show", "原谱'三支长号'；共用行"),
    25: ("长号", "铜管", "show", "共用行"),
    35: ("竖琴（上）", "竖琴与键盘", "show", "原谱'竖琴'为两行谱表（高音、低音），这是上行"),
    36: ("竖琴（下）", "竖琴与键盘", "show", "竖琴的下行"),
    39: ("第二小提琴（分部）", "弦乐", "show", "分部时的另一行"),
    41: ("中提琴（分部）", "弦乐", "show", "分部时的另一行"),
}

FINALE = {
    1: ("短笛", "木管", "hide", "全曲没有一个音符的空谱表"),
    2: ("长笛", "木管", "show", "原谱'四支长笛'；共用行"),
    3: ("长笛", "木管", "show", "共用行"),
    4: ("长笛", "木管", "show", "共用行"),
    5: ("长笛", "木管", "hide", "空谱表"),
    6: ("双簧管", "木管", "show", "原谱'四支双簧管'；共用行"),
    7: ("双簧管", "木管", "show", "共用行"),
    8: ("双簧管", "木管", "show", "共用行"),
    9: ("双簧管", "木管", "hide", "空谱表"),
    12: ("单簧管（降B调）", "木管", "show", "原谱'三支降B调单簧管'；标记有 1.2./3./1./2. 等，是共用行"),
    13: ("单簧管（降B调）", "木管", "show", "共用行，标记 2./3./a 2"),
    14: ("单簧管（降B调）", "木管", "show", "共用行，标记 3."),
    15: ("单簧管（A调）", "木管", "show", "换 A 调单簧管用的行；标记有 1.–4.、'D-Klar. übernimmt 4'，是共用行"),
    16: ("单簧管（A调）", "木管", "show", "共用行，标记 2./3."),
    17: ("单簧管（A调）", "木管", "hide", "空谱表"),
    18: ("单簧管（A调）", "木管", "show", "共用行，标记 2./3./'4. Klar. nimmt D-Klar.'"),
    19: ("单簧管（A调）", "木管", "show", "共用行，标记 4."),
    20: ("低音单簧管", "木管", "show", "原谱只有一支低音单簧管（降B调）"),
    21: ("低音单簧管", "木管", "merge:20", "A 调低音单簧管的行，与第 20 行没有同时发声的小节（标记 in A / in B），是同一人换乐器"),
    22: ("巴松", "木管", "show", "原谱'四支巴松'；共用行"),
    23: ("巴松", "木管", "show", "共用行"),
    24: ("巴松", "木管", "show", "共用行"),
    25: ("巴松", "木管", "show", "共用行"),
    26: ("巴松", "木管", "show", "只有第 667–671 小节，标记 a 2./3./4."),
    **{i: ("圆号", "铜管", "show", "原谱'八支圆号'；共用行") for i in range(28, 36)},
    **{i: ("小号（F调）", "铜管", "show", "原谱'六支小号'；共用行（标记写着是哪几位）") for i in range(36, 43)},
    **{i: ("小号（降B调）", "铜管", "show", "换降B调小号用的行（标记 in B）；共用行") for i in range(43, 51)},
    51: ("长号", "铜管", "show", "原谱'三支长号'；共用行"),
    52: ("长号", "铜管", "show", "共用行"),
    53: ("长号", "铜管", "show", "共用行"),
    54: ("长号", "铜管", "show", "共用行"),
    55: ("低音长号", "铜管", "show", "原谱'低音长号'，标记 4.（第四长号）"),
    57: ("定音鼓", "打击乐", "show", "原谱'定音鼓'；标记有 1./2./a 2，两位鼓手共用"),
    58: ("定音鼓 2", "打击乐", "show", "只在第 617–644 小节，标记全是 2.（第二位鼓手）"),
    60: ("钟（远处）", "打击乐", "show", "谱面标记 'in der Ferne'（在远处）；原谱首页没有单列，具体是哪种钟待核对"),
    61: ("牛铃 · 低音钟", "打击乐", "show", "原谱 'Herdenglocken / Tiefes Glockengeläute'"),
    67: ("桦条鞭", "打击乐", "show", "原谱 'Rute'"),
    68: ("大锤", "打击乐", "show", "原谱 'Hammer'"),
    69: ("竖琴（上）", "竖琴与键盘", "show", "原谱'竖琴'为两行谱表，这是上行"),
    70: ("竖琴（下）", "竖琴与键盘", "show", "竖琴的下行"),
    71: ("竖琴 2（上）", "竖琴与键盘", "show", "只在第 288–293 小节，标记 2.（第二竖琴）"),
    72: ("竖琴 2（下）", "竖琴与键盘", "show", "第二竖琴的下行，标记 2."),
    73: ("钢片琴（上）", "竖琴与键盘", "show", "原谱'钢片琴'为两行谱表，这是上行"),
    74: ("钢片琴（下）", "竖琴与键盘", "show", "钢片琴的下行"),
    75: ("小提琴（另一行）", "弦乐", "show", "只在第 566–590 小节，与第一小提琴同时出现；是独奏还是分部，待对照原谱核对"),
    76: ("第一小提琴", "弦乐", "show", ""),
    77: ("第一小提琴（分部）", "弦乐", "show", "只在第 561–565 小节，分部时的另一行"),
    79: ("中提琴", "弦乐", "show", ""),
    80: ("中提琴（分部）", "弦乐", "show", "分部时的另一行"),
    81: ("大提琴", "弦乐", "show", "原谱'大提琴分三部'"),
    82: ("大提琴（分部）", "弦乐", "show", "分部时的另一行"),
}

MOVEMENTS = [("mahler sympony 6 scherzo.mxl", 3, SCHERZO), ("mahler-symphony-no-6-4th-movement.mxl", 4, FINALE)]


def marks_of(part) -> list[list]:
    """取一条谱表上所有乐手标记文字：[[小节号, 文字], ...]，按小节排序。"""
    out = []
    for el in part.recurse().getElementsByClass(m21.expressions.TextExpression):
        txt = " ".join(str(el.content).split())
        if txt and MARK.search(txt):
            mm = el.getContextByClass("Measure")
            out.append([mm.number if mm is not None else None, txt])
    return sorted(out, key=lambda x: (x[0] is None, x[0] or 0))


def main() -> None:
    result = {"_说明": "第三、四乐章谱表的中文显示名。action: show 显示 / hide 隐藏 / merge:N 与第 N 行合并为同一行。"
                       "同名的共用行同时出现时，用 marks（谱面乐手标记）区分。依据见 tools/build_labels.py。",
              "movements": {}}
    for fname, mv, table in MOVEMENTS:
        stem = pathlib.Path(fname).stem
        with open(ROOT / "out" / f"{stem}_parts.csv", encoding="utf-8-sig") as f:
            import csv
            auto = {int(r["index"]): r for r in csv.DictReader(f)}
        audit = json.loads((ROOT / "out" / f"{stem}_audit.json").read_text(encoding="utf-8"))
        parts = list(m21.converter.parse(str(ROOT / fname)).parts)
        rows = []
        for a in audit["parts"]:
            i = a["index"]
            zh, grp, action, why = table.get(i, (auto[i]["zh_name"], auto[i]["group"], "show", "沿用自动中文名"))
            if a["measures_with_notes"] == 0 and action == "show":
                action, why = "hide", "空谱表"
            rows.append({
                "index": i, "name": a["name"], "zh_name": zh, "group": grp, "action": action,
                "auto_zh_name": auto[i]["zh_name"], "changed": zh != auto[i]["zh_name"] or action != "show",
                "measures_with_notes": a["measures_with_notes"], "ranges": a["ranges"],
                "marks": marks_of(parts[i]), "reason": why,
            })
        result["movements"][str(mv)] = {"file": fname, "parts": rows}
    (ROOT / "labels.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 labels.json")


if __name__ == "__main__":
    main()
