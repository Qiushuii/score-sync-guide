"""解析卡草稿检查 + 生成审稿文档。

读 cards/m<N>.json、align/m<N>_measures.json、references.json：
- 每张卡的起止时刻（第一遍出现的小节；卡片到下一张卡开始为止，最后一张到乐章结尾）；
- 正文字数（去掉注释标记），按"每秒 5 字 + 2 秒"算需要的停留时间，不够就标出；
- 估算排版行数（右栏 560 像素，正文每行约 26 字），超过一页能放下的行数就标出；
- 注释角标按出现顺序编号，引用的 id 必须在 references.json 里。
输出 analysis/m<N>_卡片审稿.md（给用户看的版本：角标换成上标数字）。
用法：python -X utf8 tools/cards_review.py 4
"""
import json
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHARS_PER_LINE = 24          # 560 宽右栏、21 像素正文（2026-11-08 实测）
MAX_LINES = 16.5             # 实测：第 139 小节卡（303 字、4 段、1 条术语）正好填满；每条术语小字约占 1.25 行
SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def fmt(s):
    return f"{int(s // 60)}:{s % 60:04.1f}"


def main():
    mv = int(sys.argv[1])
    cards = json.loads((ROOT / "cards" / f"m{mv}.json").read_text(encoding="utf-8"))["cards"]
    rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
    refs = json.loads((ROOT / "references.json").read_text(encoding="utf-8"))["refs"]
    refs = refs if isinstance(refs, dict) else {r["id"]: r for r in refs}
    first = {}
    for r in rows:
        first.setdefault(r["measure"], r["audio_sec"])
    end = rows[-1]["audio_sec"] + rows[-1]["audio_dur"]
    num, order, problems, out = {}, [], [], []
    out.append(f"# 第 {mv} 乐章解析卡审稿\n")
    out.append("> 由 `tools/cards_review.py` 从 `cards/m%d.json` 生成，改稿请改 json。时刻是本乐章音轨时间（已对齐）。卡片上的上标数字是全片统一的注释编号（同一出处共用一个编号），〔〕里是对应的出处（只在审稿文档里显示）；参考资料按编号在片尾列出，见 analysis/片尾参考资料.md。每页文字一次性显示，阅读时间约为停留时间的一半。\n" % mv)
    occ = {}
    for i, r in enumerate(rows):
        occ.setdefault(r["measure"], []).append(i)
    row_of = lambda c: occ[c["m"]][c.get("pass", 1) - 1]      # pass=2：反复的第二遍
    for k, c in enumerate(cards):
        k0 = row_of(c)
        k1 = row_of(cards[k + 1]) if k + 1 < len(cards) else len(rows)
        if k1 <= k0:
            problems.append(f"第 {c['m']} 小节卡：下一张卡不在它后面（顺序或 pass 写错）")
        t0 = rows[k0]["audio_sec"]
        t1 = rows[k1]["audio_sec"] if k1 < len(rows) else end
        m1 = rows[k1 - 1]["measure"]
        # 卡片里提到的小节按本页这一遍的时刻算
        first = {r["measure"]: r["audio_sec"] for r in rows[k0:k1]} | {m: v for m, v in first.items() if m not in {r["measure"] for r in rows[k0:k1]}}
        body = c["body"]
        for rid in re.findall(r"〔ref:([^〕]+)〕", body):
            if rid not in refs:
                problems.append(f"第 {c['m']} 小节卡：参考资料 {rid} 不在 references.json")
            if rid not in num:
                num[rid] = len(num) + 1; order.append(rid)
        # 一页文字一次性显示（用户 2026-11-08）。原则：阅读时间约为停留时间的一半，即读 30 秒的文字讲接下来约 60 秒的音乐。
        paras = [(c["m"], x) for x in body.split("\n")]
        plain = re.sub(r"〔ref:[^〕]+〕", "", body)
        n = len(plain.replace("\n", ""))
        need = n / 5 + 2
        lines = sum(math.ceil(len(p) / CHARS_PER_LINE) for p in plain.split("\n")) + 0.5 * (plain.count("\n"))
        flag = []
        # 讲得太超前：正文提到的本页小节，离本页出现的时刻超过“两倍阅读时间 + 15 秒”
        ahead = [b for b in map(int, re.findall(r"第 (\d+)(?:–\d+)? 小节", plain)) if c["m"] <= b <= m1 and first[b] - t0 > 2 * need + 15]
        if ahead:
            flag.append(f"⚠ 提到第 {ahead[0]} 小节，离本页出现已 {first[ahead[0]] - t0:.0f} 秒，讲得太超前")
        if t1 - t0 < need:
            flag.append(f"⚠ 停留 {t1 - t0:.0f} 秒，不够读（需 {need:.0f} 秒）")
        cap = MAX_LINES - 1.25 * len(c.get("gloss", []))
        if lines > cap:
            flag.append(f"⚠ 约 {lines:.0f} 行，一页放不下")
        problems += [f"第 {c['m']} 小节卡：{f}" for f in flag]
        # 卡片上是全片统一的上标编号（cards/refs_numbering.json）；审稿文档在编号后注明出处，方便核对
        gnum = json.loads((ROOT / "cards" / "refs_numbering.json").read_text(encoding="utf-8")) if (ROOT / "cards" / "refs_numbering.json").exists() else {}
        sup_ = lambda x: re.sub(r"〔ref:([^〕]+)〕", lambda mo: str(gnum.get(mo.group(1), "*")).translate(SUP) + f"〔{refs[mo.group(1)].get('short') or mo.group(1)}〕", x)
        shown = "\n".join(sup_(x) for _, x in paras)
        out.append(f"## {k + 1}. {c['title']}\n")
        out.append(f"第 {c['m']}–{m1} 小节 · 录音 {fmt(t0)}–{fmt(t1)}（停留 {t1 - t0:.0f} 秒）· 正文 {n} 字（读完约需 {need:.0f} 秒）"
                   + f" · 占卡片约 {100 * lines / cap:.0f}%" + (f" · 高亮：{'、'.join(c['focus'])}" if c.get("focus") else "") + "\n")
        out += [p + "\n" for p in shown.split("\n")]
        for term, ex in c.get("gloss", []):
            out.append(f"> **{term}**：{ex}\n")
        if flag:
            out.append("\n".join(flag) + "\n")
    notes = json.loads((ROOT / "cards" / f"m{mv}.json").read_text(encoding="utf-8")).get("review_notes", [])
    if notes:
        out.append("## 需要你判断或待核实的地方\n")
        out += [f"- {x}" for x in notes]
        out.append("")
    out.append("## 本乐章用到的参考资料（编号见 analysis/片尾参考资料.md）\n")
    for rid in order:
        r = refs[rid]
        who = r.get("author") or r.get("org") or ""
        cnt = sum(c["body"].count(f"〔ref:{rid}〕") for c in cards)
        out.append(f"- {who}，{r.get('title', '')}" + ("（转述）" if r.get("secondhand") else "") + f"：引用 {cnt} 处\n")
    p = ROOT / "analysis" / f"m{mv}_卡片审稿.md"
    p.write_text("\n".join(out), encoding="utf-8")
    print(f"{len(cards)} 张卡，注释 {len(order)} 条 → {p}")
    print("\n".join(problems) if problems else "检查通过：停留时间、行数、参考资料都没问题")


if __name__ == "__main__":
    main()
