"""按 scores_fixed/corrections.json 生成修正后的 mxl 副本（原文件不动）。

做法：从原 mxl（zip）里取出 MusicXML 文本，只在指定声部、指定小节里替换指定的那一段文字，
其余字节原样保留，再打包成 scores_fixed/<原文件名>。每条修正都检查出现次数，不符就报错、不写文件。
不用 music21 读入再整体写出，是为了不让整份谱子被重新生成、悄悄改掉别的内容。

用法：venv\\Scripts\\python.exe -X utf8 tools\\fix_mxl.py
"""
import hashlib
import json
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = {1: "symphony-6-movement-1-mahler.mxl", 2: "symphony-6-movement-2-mahler.mxl",
         3: "mahler sympony 6 scherzo.mxl", 4: "mahler-symphony-no-6-4th-movement.mxl"}


def span(s: str, start_pat: str, end_tag: str, lo: int = 0, hi: int | None = None) -> tuple[int, int]:
    """在 s[lo:hi] 里找到以 start_pat 开头、到 end_tag 结束的区段，返回 (起, 止)；必须恰好一个。"""
    hi = len(s) if hi is None else hi
    hits = [m.start() for m in re.finditer(start_pat, s[lo:hi])]
    if len(hits) != 1:
        raise ValueError(f"{start_pat!r} 找到 {len(hits)} 处，应为 1 处")
    a = lo + hits[0]
    b = s.index(end_tag, a) + len(end_tag)
    return a, b


def measure_span(s: str, part: str, num: int) -> tuple[int, int]:
    pa, pb = span(s, rf'<part id="{re.escape(part)}">', "</part>")
    return span(s, rf'<measure number="{num}"[ >]', "</measure>", pa, pb)


def move_measures(s: str, s0: str, c: dict) -> str:
    """整小节搬移：把原文里 from_part 的若干小节内容搬到 to_part 的同号小节（替换掉那里的内容），
    from_part 的这些小节改成整小节休止。用于抄错声部的情况。

    - 搬过去的内容去掉 <attributes>（谱号等沿用目标声部自己的），保留音符、力度、文字。
    - 来源小节改成休止时，保留它的 <attributes>（如谱号变化），以免后面小节显示错谱号。
    - 整小节休止的时值取该小节原有音符第 1 声部的总时值。
    """
    for num in c["measures"]:
        fa, fb = measure_span(s0, c["from_part"], num)
        src = s0[fa:fb]
        open_tag = src[:src.index(">") + 1]
        body = src[len(open_tag):src.rindex("</measure>")]
        attrs = "".join(re.findall(r"<attributes>.*?</attributes>", body, re.S))
        moved = re.sub(r"<attributes>.*?</attributes>", "", body, flags=re.S)
        dur = 0
        for note in re.findall(r"<note\b.*?</note>", body, re.S):        # 第 1 声部各音符时值之和（和弦附加音不重复计）
            if "<voice>1</voice>" in note and "<chord/>" not in note and "<grace" not in note:
                dur += int(re.search(r"<duration>(\d+)</duration>", note).group(1))
        ta, tb = measure_span(s, c["to_part"], num)
        tgt = s[ta:tb]
        t_open = tgt[:tgt.index(">") + 1]
        t_attrs = "".join(re.findall(r"<attributes>.*?</attributes>", tgt, re.S))
        s = s[:ta] + t_open + t_attrs + moved + "</measure>" + s[tb:]
        rest = (f"\n      <note>\n        <rest measure=\"yes\"/>\n        <duration>{dur}</duration>\n"
                f"        <voice>1</voice>\n        </note>\n      ")
        fa2, fb2 = measure_span(s, c["from_part"], num)
        s = s[:fa2] + open_tag + attrs + rest + "</measure>" + s[fb2:]
        print(f'{c["id"]}：第 {num} 小节 {c["from_name"]} → {c["to_name"]}（{c["from_name"]} 改为整小节休止，时值 {dur}）')
    return s


def swap_measures(s: str, s0: str, c: dict) -> str:
    """两行互换：part_a 与 part_b 在 measures 范围内的小节内容对调。用于两条谱表整段抄反的情况。

    - 对调的是音符、力度、文字等；各自的 <attributes>（谱号等）和 <print>（换行换页）留在原处。
    - 音符里的 <instrument id="..."> 指向原声部的乐器编号，搬过去后改成目标声部的第一个乐器编号。
    - 内容一律取自原文快照 s0。
    """
    lo, hi = c["measures"]
    ids = {}
    for p in (c["part_a"], c["part_b"]):
        pa, pb = span(s0, rf'<score-part id="{re.escape(p)}">', "</score-part>")
        ids[p] = re.search(r'<score-instrument id="([^"]+)"', s0[pa:pb]).group(1)

    def body_of(src: str) -> str:
        open_tag = src[:src.index(">") + 1]
        body = src[len(open_tag):src.rindex("</measure>")]
        body = re.sub(r"<attributes>.*?</attributes>", "", body, flags=re.S)
        return re.sub(r"<print\b.*?</print>|<print\b[^>]*/>", "", body, flags=re.S)

    n = 0
    for num in range(lo, hi + 1):
        bodies = {}
        for p in (c["part_a"], c["part_b"]):
            fa, fb = measure_span(s0, p, num)
            bodies[p] = body_of(s0[fa:fb])
        for src_p, dst_p in ((c["part_a"], c["part_b"]), (c["part_b"], c["part_a"])):
            moved = re.sub(rf'<instrument id="{re.escape(src_p)}-[^"]*"', f'<instrument id="{ids[dst_p]}"', bodies[src_p])
            disp = c.get("unpitched_display", {}).get(dst_p)      # 可选：把带音高的写法改成该行通用的无音高写法（只影响显示位置）
            if disp:
                moved = re.sub(r"<pitch>\s*<step>[A-G]</step>\s*(?:<alter>[^<]*</alter>\s*)?<octave>\d</octave>\s*</pitch>",
                               f"<unpitched><display-step>{disp[0]}</display-step><display-octave>{disp[1]}</display-octave></unpitched>",
                               moved)
            ta, tb = measure_span(s, dst_p, num)
            tgt = s[ta:tb]
            t_open = tgt[:tgt.index(">") + 1]
            keep = "".join(re.findall(r"<print\b.*?</print>|<print\b[^>]*/>", tgt, re.S)) + \
                "".join(re.findall(r"<attributes>.*?</attributes>", tgt, re.S))
            s = s[:ta] + t_open + keep + moved + "</measure>" + s[tb:]
        n += 1
    print(f'{c["id"]}：第 {lo}–{hi} 小节 {c["name_a"]} ⇄ {c["name_b"]}（{n} 小节互换）')
    return s


OD = "<other-dynamics>other-dynamics</other-dynamics>"


def fix_dynamic(s: str, c: dict) -> str:
    """力度占位字：把 part 声部第 measure 小节里第 nth 个（0 起）"other-dynamics"换成真正的力度记号 dyn
    （MusicXML 力度元素名，如 pp、sf、sfp、fp；dyn 为 null 时整条去掉），可选 words 在同一个 direction 里加一段斜体文字。"""
    ma, mb = measure_span(s, c["part"], c["measure"])
    seg = s[ma:mb]
    hits = [m.start() for m in re.finditer(re.escape(OD), seg)]
    if not hits:                                  # 这里原本就是真的非标准力度（如 sff），不是占位字：不动
        print(f'{c["id"]}：第 {c["measure"]} 小节没有占位字（原文已是真实力度），跳过')
        return s
    if len(hits) != c.get("count", len(hits)) or c.get("nth", 0) >= len(hits):
        raise ValueError(f'{c["id"]}：{c["part"]} 第 {c["measure"]} 小节占位字 {len(hits)} 处，与登记不符')
    h = hits[c.get("nth", 0)]
    if c.get("dyn"):
        STD = {"p", "pp", "ppp", "pppp", "f", "ff", "fff", "ffff", "mp", "mf", "sf", "sfp", "sfpp", "fp", "rf", "rfz", "sfz", "sffz", "fz", "pf", "n", "sfzp"}
        new = f'<{c["dyn"]}/>' if c["dyn"] in STD else f'<other-dynamics>{c["dyn"]}</other-dynamics>'   # 如 ffp：MusicXML 没有这个元素（占位字就是这样来的）
        seg2 = seg[:h] + new + seg[h + len(OD):]
        if c.get("words"):
            end = seg2.index("</direction-type>", h) + len("</direction-type>")
            extra = ("\n          <direction-type>\n            <words font-style=\"italic\">" + c["words"]
                     + "</words>\n          </direction-type>")
            seg2 = seg2[:end] + extra + seg2[end:]
    else:                                         # 原谱这里没有力度记号：去掉整个 dynamics 所在的 direction-type
        a = seg.rindex("<direction-type>", 0, h); b = seg.index("</direction-type>", h) + len("</direction-type>")
        seg2 = seg[:a] + seg[b:]
    print(f'{c["id"]}：{c.get("part_name", c["part"])} 第 {c["measure"]} 小节 → {c.get("dyn") or "（无）"}{" " + c["words"] if c.get("words") else ""}')
    return s[:ma] + seg2 + s[mb:]


def main() -> None:
    table = json.loads((ROOT / "scores_fixed" / "corrections.json").read_text(encoding="utf-8"))
    by_mv: dict[int, list] = {}
    for c in table["corrections"]:
        by_mv.setdefault(c["movement"], []).append(c)
    renum = {r["movement"]: r for r in table.get("renumber", [])}
    for mv in renum:
        by_mv.setdefault(mv, [])
    for mv, cs in sorted(by_mv.items()):
        src = ROOT / FILES[mv]
        before = hashlib.sha256(src.read_bytes()).hexdigest()
        with zipfile.ZipFile(src) as z:
            infos = z.infolist()
            data = {i.filename: z.read(i.filename) for i in infos}
        xml_name = next(n for n in data if not n.startswith("META-INF") and n.endswith((".xml", ".musicxml")))
        s = data[xml_name].decode("utf-8")
        s0 = s                                        # 原文快照：搬移类修正一律从原文取来源，不受前面修正影响
        for c in cs:
            if c.get("type") == "move_measures":
                s = move_measures(s, s0, c)
                continue
            if c.get("type") == "swap_measures":
                s = swap_measures(s, s0, c)
                continue
            if c.get("type") == "dynamic":
                s = fix_dynamic(s, c)
                continue
            pa, pb = span(s, rf'<part id="{re.escape(c["part"])}">', "</part>")
            ma, mb = span(s, rf'<measure number="{c["measure"]}"[ >]', "</measure>", pa, pb)
            seg = s[ma:mb]
            n = seg.count(c["find"])
            if n != c["expect"]:
                sys.exit(f'{c["id"]}：{c["part"]} 第 {c["measure"]} 小节里找到 {n} 处，应为 {c["expect"]} 处，未写文件')
            s = s[:ma] + seg.replace(c["find"], c["replace"]) + s[mb:]
            print(f'{c["id"]}：{c["part_name"]} 第 {c["measure"]} 小节  {c["find"]}  →  {c["replace"]}')
        # 排练记号整体重编（改成 PDF 版本的编号）：只改 from 范围内的数字，个数必须等于 expect
        if mv in renum:
            r = renum[mv]
            lo, hi, off = r["from"][0], r["from"][1], r["offset"]
            changed = []

            def sub(m):
                v = m.group(2)
                if v.isdigit() and lo <= int(v) <= hi:
                    changed.append(f"{v}→{int(v) + off}")
                    return f"{m.group(1)}{int(v) + off}{m.group(3)}"
                return m.group(0)

            s2 = re.sub(r"(<rehearsal[^>]*>)([^<]*)(</rehearsal>)", sub, s)
            if len(changed) != r["expect"]:
                sys.exit(f"第 {mv} 乐章排练记号应改 {r['expect']} 个，实际 {len(changed)} 个，未写文件")
            s = s2
            print(f"第 {mv} 乐章排练记号重编 {len(changed)} 个：{changed[0]} … {changed[-1]}")
        data[xml_name] = s.encode("utf-8")
        out = ROOT / "scores_fixed" / FILES[mv]
        if out.exists():                              # 内容没变就不重写（渲染可能正在读这个文件）
            with zipfile.ZipFile(out) as z:
                if z.read(xml_name) == data[xml_name]:
                    print(f"{out.relative_to(ROOT)} 内容未变，不重写")
                    continue
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for i in infos:
                z.writestr(i, data[i.filename])
        after = hashlib.sha256(src.read_bytes()).hexdigest()
        assert before == after, "原文件被改动了！"
        print(f"已写 {out.relative_to(ROOT)}（原文件指纹未变）")


if __name__ == "__main__":
    main()
