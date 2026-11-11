"""全片注释编号与片尾参考资料（2026-11-09，用户：赏析写完后给注释编号，同一出处共享同一个编号）。

按片中顺序（第一乐章 → 终乐章，卡片顺序，正文从前到后）扫描 cards/m1..m4.json 里的 〔ref:id〕，
第一次出现的出处依次编号，之后再出现沿用同一编号。
输出：
  cards/refs_numbering.json     {id: 编号}，build_score.py 与 tools/cards_review.py 读它来显示上标数字
  analysis/片尾参考资料.md       片尾要列的参考资料（按编号），附每条被引用的位置，以及可信度说明
用法：python -X utf8 tools/build_refs.py
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    refs = json.loads((ROOT / "references.json").read_text(encoding="utf-8"))
    by = {r["id"]: r for r in refs["refs"]}
    claims = refs.get("claims", {})
    claims = claims if isinstance(claims, dict) else {c["id"]: c for c in claims}
    num, where = {}, {}
    for mv in (1, 2, 3, 4):
        p = ROOT / "cards" / f"m{mv}.json"
        if not p.exists():
            continue
        for c in json.loads(p.read_text(encoding="utf-8"))["cards"]:
            for rid in re.findall(r"〔ref:([^〕]+)〕", c["body"]):
                num.setdefault(rid, len(num) + 1)
                tag = f"第{mv}乐章第 {c['m']} 小节" + ("（反复）" if c.get("pass") == 2 else "")
                where.setdefault(rid, [])
                if tag not in where[rid]:
                    where[rid].append(tag)
    (ROOT / "cards" / "refs_numbering.json").write_text(json.dumps(num, ensure_ascii=False, indent=1), encoding="utf-8")
    out = ["# 片尾参考资料", "",
           "> 由 `tools/build_refs.py` 生成。编号与卡片上的上标数字一致：同一出处在全片共用一个编号，按第一次出现的顺序编。",
           "> 卡片上的断言出自谱面数据的，不加注；只有来自文献的才有编号。", ""]
    for rid, n in sorted(num.items(), key=lambda x: x[1]):
        r = by.get(rid, {})
        who = r.get("author", "")
        if r.get("cite"):
            line = f"{n}. {r['cite']}"
        else:
            line = f"{n}. {who}，{r.get('title', rid)}"
            if r.get("url"):
                line += f"，{r['url']}"
            if r.get("accessed"):
                line += f"（访问于 {r['accessed']}）"
        if r.get("secondhand"):
            line += "。转述"
        out.append(line)
        out.append(f"   - 引用位置：{'、'.join(where[rid])}")
    out += ["", "## 可信度说明（片尾一并列出）", ""]
    for cid, c in claims.items():
        note = c.get("credibility") or c.get("片尾说明") or c.get("credibility_note")
        if note:
            out.append(f"- {c.get('text_zh') or c.get('text') or cid}：{note}")
    out += ["- 乐谱来自社区 MusicXML，可能有个别错误；小节号按本谱。"]
    (ROOT / "analysis" / "片尾参考资料.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    # 片尾画面用的数据（demo/credits.html 读）：书目用 references.json 的 cite（芝加哥格式书目体），
    # 说明只列卡片里实际用到、且写了给观众看的 credits_zh 的轶事
    used = set(num)
    notes = [c["credits_zh"] for c in claims.values() if c.get("credits_zh") and used & set(c.get("refs", []))]
    credits = {"refs": [{"n": n, "cite": by.get(rid, {}).get("cite") or by.get(rid, {}).get("title", rid),
                         "secondhand": bool(by.get(rid, {}).get("secondhand"))}
                        for rid, n in sorted(num.items(), key=lambda x: x[1])],
               "notes": notes}
    (ROOT / "demo" / "credits_data.js").write_text("window.CREDITS = " + json.dumps(credits, ensure_ascii=False, indent=1) + ";\n",
                                                   encoding="utf-8")
    print(f"共 {len(num)} 个出处：" + "，".join(f"{n}={rid}" for rid, n in sorted(num.items(), key=lambda x: x[1])))


if __name__ == "__main__":
    main()
