"""排谱：用 Verovio 把某个乐章的一段排成单行 SVG，并导出播放页需要的全部数据。

只读原 mxl，输出写到 --out 指定的目录（默认 demo/build/）的 data.js。
需要装了 verovio 和 music21 的 Python（系统 Python）。

用法：
    python -X utf8 demo/build_score.py                          # 第二乐章第 0–10 小节（演示片段）
    python -X utf8 demo/build_score.py --movement 4 --range 1-12 --out demo/build_m4
--range 是 Verovio 的位置范围（从 1 起）：第二、三乐章的位置 1 是谱面第 0 小节，第一、四乐章的位置 1 是第 1 小节。
"""
import argparse
import csv
import json
import pathlib
import os
import re
import warnings

import music21 as m21
import verovio

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent

FILES = {
    1: "symphony-6-movement-1-mahler.mxl",
    2: "symphony-6-movement-2-mahler.mxl",
    3: "mahler sympony 6 scherzo.mxl",
    4: "mahler-symphony-no-6-4th-movement.mxl",
}
# 乐章标题：中文序号 + 录音内嵌标签里的速度术语
# 乐章名、原文速度标记、中文译名（用户 2026-11-08：顶栏原文与中文一起显示）
TITLES = {1: ("第一乐章", "Allegro energico, ma non troppo", "有力的快板，但不过分"),
          2: ("第二乐章", "Andante moderato", "中庸的行板"),
          3: ("第三乐章", "Scherzo. Wuchtig", "谐谑曲：沉重有力地"),
          4: ("第四乐章", "Finale. Allegro moderato", "终曲：中庸的快板")}


# ---------------- 乐手标记 ----------------
UNISON = re.compile(r"\ba\s?(\d)\.?")


def parse_players(text: str):
    """把谱面上的乐手标记解析成 {players, unison, solo}；不是乐手标记就返回 None。

    "1.2.3." → 1、2、3；"4.5.6." → 4、5、6；"2. u. 3." → 2、3；"a 2" → 两人齐奏；"1. Solo" → 1 号独奏。
    "4. Klar. nimmt D-Klar." 这类换乐器提示、"offen 1."以外夹杂其他词的文字一律不算，
    因为它们说的是"谁要换乐器"，不是"谁在奏"。
    """
    t = " ".join(text.split())
    solo = bool(re.search(r"\bsolo\b", t, re.I))
    # 允许夹带的说明：独奏、开放音、调名（in F）、弱音器
    s = re.sub(r"\bsolo\b|\boffen\b|\bin\s+[A-H]\b\.?|mit\s+D[äa]mpfer|D[äa]mpfer\s+auf|mit\s+Sord\.?|[()]",
               " ", t, flags=re.I)
    un = UNISON.search(s)
    s2 = UNISON.sub(" ", s)
    if not re.fullmatch(r"[\d\.\s,&]*(?:u\.[\d\.\s]*)*", s2):
        return None
    players = sorted({int(d) for d in re.findall(r"(\d)\.", s2)})
    if not players and not un:
        return None
    return {"players": players, "unison": int(un.group(1)) if un else None, "solo": solo}


def player_marks(part) -> list:
    out = []
    for el in part.recurse().getElementsByClass(m21.expressions.TextExpression):
        p = parse_players(str(el.content))
        if p is None:
            continue
        mm = el.getContextByClass("Measure")
        if mm is None or mm.number is None:
            continue
        out.append({"m": mm.number, **p})
    return sorted(out, key=lambda x: x["m"])


# ---------------- 声部表 ----------------
def load_parts(mv: int, fname: str) -> list:
    """优先用 labels.json（第三、四乐章已人工判断）；没有的乐章用 analyze.py 的 parts.csv。"""
    stem = pathlib.Path(fname).stem
    with open(ROOT / "out" / f"{stem}_parts.csv", encoding="utf-8-sig") as f:
        rows = {int(r["index"]): r for r in csv.DictReader(f)}
    lab = {}
    lp = ROOT / "labels.json"
    if lp.exists():
        mvd = json.loads(lp.read_text(encoding="utf-8"))["movements"].get(str(mv))
        if mvd:
            lab = {p["index"]: p for p in mvd["parts"]}
    parts = []
    for i in sorted(rows):
        r, l = rows[i], lab.get(i)
        action = l["action"] if l else "show"
        parts.append({
            "en": r["name"], "zh": l["zh_name"] if l else r["zh_name"], "group": l["group"] if l else r["group"],
            "hide": action == "hide",
            "row": int(action.split(":")[1]) if action.startswith("merge:") else i,   # 合并时与目标共用一行
        })
    return parts


def meter_at_start(xml: str, first_index: int) -> str:
    """全片分段渲染（2026-11-09）：Verovio 只在拍号变化处画拍号，从乐章中间开始的一段谱头就没有拍号。
    在每个声部第 first_index 个小节（从 0 起）补上当时生效的 <time>（已有就不动）。只改内存里的副本，只影响显示。"""
    def fix_part(pm):
        part = pm.group(0)
        out, last, pos = [], None, 0
        for k, mm in enumerate(re.finditer(r"<measure\b[^>]*>.*?</measure>", part, re.S)):
            mt = mm.group(0)
            if k == first_index and last and "<time" not in mt:
                if "<attributes>" in mt:
                    m2 = re.search(r"</key>|</divisions>", mt)
                    at = m2.end() if m2 else mt.index("<attributes>") + len("<attributes>")
                    mt = mt[:at] + last + mt[at:]
                else:
                    at = mt.index(">") + 1
                    mt = mt[:at] + "<attributes>" + last + "</attributes>" + mt[at:]
            t = re.findall(r"<time\b[^>]*>.*?</time>", mm.group(0), re.S)
            if t:
                last = t[-1]
            out.append(part[pos:mm.start()] + mt); pos = mm.end()
        out.append(part[pos:])
        return "".join(out)
    return re.sub(r"<part\s[^>]*>.*?</part>", fix_part, xml, flags=re.S)     # \s：不要匹配到 <part-list>


def apply_alignment(data: dict, mv: int, timemap: list, meas_n: dict, first_row=None, last_row=None, anchor_row=None) -> None:
    """把时间表换成录音时间（音频是主时钟）。

    align/m<N>_measures.json 给出每个演奏小节开头的四分音符位置 q_start 与录音秒数 audio_sec；
    小节内部按四分音符线性插值。Verovio 时间表的 qstamp 从所选范围的第一个小节算起，
    用第一个 measureOn 事件的小节号找出它在对齐表里的 q_start，求出两者的差。
    片段的 t=0 定在所选范围第一个小节开头在录音里的时刻；data["audio"] 记下音轨文件和这个偏移，
    data["clipEnd"] 是片段结束时刻（倒数第二个所选小节的开头，留一小节给右侧谱面）。
    first_row / last_row：直接指定片段首末小节在对齐表里的行号（反复的第二遍与第一遍小节号相同，
    要靠行号区分）；给了 last_row 时片段结束在该行小节的结尾。
    """
    import bisect
    ap = ROOT / "align" / f"m{mv}_measures.json"
    if not ap.exists():
        print("没有对齐结果，画面按谱面速度")
        return
    al = json.loads(ap.read_text(encoding="utf-8"))
    rows = al["measures"]
    Q = [r["q_start"] for r in rows] + [rows[-1]["q_start"] + rows[-1]["q_len"]]
    A = [r["audio_sec"] for r in rows] + [rows[-1]["audio_sec"] + rows[-1]["audio_dur"]]
    # 小节内拍点钉点（align/manual_regions.json 的 "beats"：{行号: [[小节内四分音符位置, 录音秒], ...]}）。
    # 用于小节内部速度很不均匀的地方（如终乐章结尾的 Pesante），否则小节内按线性插值。
    mr = ROOT / "align" / "manual_regions.json"
    if mr.exists():
        beats = json.loads(mr.read_text(encoding="utf-8")).get(str(mv), {}).get("beats", {})
        pts = sorted(zip(Q, A))
        for k, lst in beats.items():
            r = rows[int(k)]
            for qo, sec in lst:
                if 0 < qo < r["q_len"]:
                    pts.append((r["q_start"] + qo, float(sec)))
        pts.sort()
        Q = [q for q, _ in pts]; A = [a for _, a in pts]
        assert all(A[i] < A[i + 1] for i in range(len(A) - 1)), "拍点钉点与小节时刻冲突（时间不递增）"
    # 逐起音时间表（align/m<N>_ssv_chain.json，align/ssv_align.py 借 score-sync-video 算法生成）代替小节内匀速插值；
    # 它覆盖不到的首尾仍用上面的小节表。2026-11-09 试验，用户 2026-11-10 听 A/B 后选定：第一至第三乐章默认启用，
    # 终乐章自动检验好坏参半，待听后再定。环境变量 TIMEMAP=off 关闭，TIMEMAP=<路径> 指定别的文件。
    SSV_ON = {1, 2, 3, 4}          # 终乐章：用户 2026-11-10 听终乐章 A/B 后也选 B
    tm = os.environ.get("TIMEMAP")
    if tm is None and mv in SSV_ON and (ROOT / "align" / f"m{mv}_ssv_chain.json").exists():
        tm = f"align/m{mv}_ssv_chain.json"
    if tm and tm != "off":
        ch = json.loads((ROOT / tm).read_text(encoding="utf-8"))
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "align"))
        from ssv_align import inputs_digest
        if ch.get("inputs") != inputs_digest(mv):
            raise SystemExit(f"{tm} 是按旧的小节表或钉点算的，请先重跑：python -X utf8 -W ignore align/ssv_align.py {mv} （或设 TIMEMAP=off）")
        cq, ca = ch["q"], ch["sec"]
        pts = [(q, a) for q, a in zip(Q, A) if q < cq[0] - 1e-6] + list(zip(cq, ca)) + [(q, a) for q, a in zip(Q, A) if q > cq[-1] + 1e-6]
        Q = [q for q, _ in pts]; A = [a for _, a in pts]
        assert all(A[i] < A[i + 1] for i in range(len(A) - 1)), "TIMEMAP 时间不递增"
        print(f"小节内时间改用 {tm}（{len(cq)} 个点）")

    def audio_at(q: float) -> float:
        k = min(max(bisect.bisect_right(Q, q) - 1, 0), len(Q) - 2)
        return A[k] + (A[k + 1] - A[k]) * (q - Q[k]) / (Q[k + 1] - Q[k])

    # 时间表覆盖整个乐章；取所选范围内的第一个小节（只有它们有 @n）
    first = next(ev for ev in timemap if ev.get("measureOn") and meas_n.get(ev["measureOn"]) is not None)
    m_first = meas_n.get(first["measureOn"])
    if anchor_row is not None:
        # 全片分段渲染（2026-11-09）：所选范围前面多排几个小节作衔接，t=0 定在对齐表第 anchor_row 行的小节开头。
        # 前面多出的小节按四分音符位置线性往前推，落到对齐表前几行的录音时刻上（单调，不会卡住）
        first = next(ev for ev in timemap if ev.get("measureOn") and meas_n.get(ev["measureOn"]) == rows[anchor_row]["measure"])
        k_first = anchor_row
    elif first_row is not None:
        k_first = first_row
        assert rows[k_first]["measure"] == m_first, f"第 {first_row} 行是第 {rows[k_first]['measure']} 小节，与所选范围首小节 {m_first} 不符"
    else:
        k_first = next(i for i, r in enumerate(rows) if r["measure"] == m_first)    # 不含反复时第一次出现即可
    q_off = rows[k_first]["q_start"] - first["qstamp"]
    t0 = audio_at(first["qstamp"] + q_off)
    for ev in timemap:
        ev["tstamp_score"] = ev["tstamp"]
        ev["tstamp"] = round(1000 * (audio_at(ev["qstamp"] + q_off) - t0), 1)
    # 时间表覆盖整个乐章；所选范围内的小节才有 @n（其余为 None），片段结尾只看所选小节
    m_starts = [ev for ev in timemap if ev.get("measureOn") and meas_n.get(ev["measureOn"]) is not None]
    end_ev = m_starts[-2] if len(m_starts) >= 2 else m_starts[-1]
    data["audio"] = {"file": al["audio"], "offset": round(t0, 3)}
    data["clipEnd"] = round(end_ev["tstamp"] / 1000, 3)
    if last_row is not None:
        r = rows[last_row]
        data["clipEnd"] = round(r["audio_sec"] + r["audio_dur"] - t0, 3)
        data["lastRowIsEnd"] = last_row == len(rows) - 1     # 片段一直到乐章结尾：播放页在结尾让卡片等淡出
    data["alignment"] = {"file": str(ap.relative_to(ROOT)), "status": al.get("status"), "created": al.get("created")}
    print(f"已按对齐结果换成录音时间：片段从录音 {t0:.2f} 秒开始，长 {data['clipEnd']:.2f} 秒")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--movement", type=int, default=2)
    ap.add_argument("--range", default="1-11")
    ap.add_argument("--out", default=str(ROOT / "demo" / "build"))
    ap.add_argument("--no-align", action="store_true", help="不用对齐结果，画面按谱面速度（无声）")
    ap.add_argument("--first-row", type=int, default=None, help="片段首小节在对齐表里的行号（反复第二遍用）")
    ap.add_argument("--last-row", type=int, default=None, help="片段末小节在对齐表里的行号（片段结束在该小节结尾）")
    ap.add_argument("--meter-at-start", action="store_true", help="所选范围第一小节补上当时生效的拍号（全片分段渲染：谱头要有拍号）")
    ap.add_argument("--anchor-row",type=int, default=None, help="t=0 定在对齐表这一行的小节开头（全片分段渲染：前面多排几个小节作衔接）")
    args = ap.parse_args()
    mv, fname = args.movement, FILES[args.movement]
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    # 有对照原谱修正过的副本（tools/fix_mxl.py 生成）就用副本，否则用原文件
    fixed = ROOT / "scores_fixed" / fname
    src = fixed if fixed.exists() else ROOT / fname
    print("谱面来源：", src.relative_to(ROOT))

    tk = verovio.toolkit()
    tk.setOptions({
        "breaks": "none", "scale": 42, "spacingLinear": 0.9, "spacingNonLinear": 0.6,
        "header": "none", "footer": "none", "adjustPageHeight": True,
        "pageMarginLeft": 0, "pageMarginTop": 60, "svgBoundingBoxes": False,
    })
    # 兜底（2026-11-07）：社区 mxl 导入失败留下的力度占位字"other-dynamics"，对照原谱查到的已在副本里改正
    # （scores_fixed/corrections.json）；还没查到的，排谱前在内存里去掉，免得把这串英文画在谱面上。只影响显示。
    import re, zipfile
    with zipfile.ZipFile(src) as z:
        xml_name = next(n for n in z.namelist() if not n.startswith("META-INF") and n.endswith((".xml", ".musicxml")))
        xml = z.read(xml_name).decode("utf-8")
    xml, n_od = re.subn(r"<direction-type>\s*<dynamics[^>]*>\s*<other-dynamics>other-dynamics</other-dynamics>\s*</dynamics>\s*</direction-type>", "", xml)
    xml = re.sub(r"<direction[^>]*>\s*(<sound[^>]*/>\s*)?</direction>", "", xml)    # 只剩空壳的 direction 一并去掉
    if n_od:
        print(f"未修正的力度占位字：{n_od} 处，排谱时隐藏")
    # 文字记号里的乐谱字体私有区字符（如 U+ECA5）普通字体里没有，会显示成方框（2026-11-08，终乐章第 685、727 小节竖琴）
    xml = re.sub(r"(<words[^>]*>)([^<]*)(</words>)", lambda m_: m_.group(1) + re.sub(r"[-]", "", m_.group(2)) + m_.group(3), xml)
    if args.meter_at_start:
        xml = meter_at_start(xml, int(args.range.split("-")[0]) - 1)
    if not tk.loadData(xml):
        raise SystemExit("Verovio 载入失败")
    tk.select({"measureRange": args.range})
    tk.redoLayout()
    svg = tk.renderToSVG(1)
    def _bad(svg_):                                                    # 排版失败：页面宽度只剩 500（横向没排开）或高度上千万（被某个元素撑高）
        m_ = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg_)
        return bool(m_) and (int(m_.group(1)) <= 500 or int(m_.group(2)) > 10_000_000)
    # 2026-11-08：谐谑曲第 401 小节竖琴的琶音记号会让 Verovio 的整段排版失败（谱面空白），先去掉琶音记号重排，仍不行再去掉圆滑线。只影响显示
    for _what, _pat in (("琶音记号", r'<arpeggiate [^>]*/>'), ("圆滑线", r'<slur [^>]*/>')):
        if not _bad(svg):
            break
        print(f"排版异常，去掉{_what}后重排")
        xml = re.sub(_pat, "", xml)
        tk.loadData(xml); tk.select({"measureRange": args.range}); tk.redoLayout()
        svg = tk.renderToSVG(1)
    timemap = tk.renderToTimemap({"includeMeasures": True})

    # 控制事件（力度、文字、连线等）属于哪个谱表
    ctrl = {}
    pat = r'<g id="([^"]+)" class="(dir|dynam|hairpin|slur|tie|tempo|fermata|trill|pedal|octave|breath|harm|reh|' \
          r'mordent|turn|arpeg|fing|phrase|lv|gliss|bracketSpan|ornam|caesura)\b'
    for el_id, kind in re.findall(pat, svg):
        attr = tk.getElementAttr(el_id) or {}
        staff = attr.get("staff")
        ctrl[el_id] = {"kind": kind, "staff": int(staff.split()[0]) if staff else None,
                       "startid": (attr.get("startid") or "").lstrip("#") or None,
                       "endid": (attr.get("endid") or "").lstrip("#") or None}

    # 时间表里每个小节开头对应的谱面小节号（Verovio 的 @n）
    meas_n = {}
    for ev in timemap:
        mid = ev.get("measureOn")
        if mid:
            n = (tk.getElementAttr(mid) or {}).get("n")
            meas_n[mid] = int(n) if n and n.lstrip("-").isdigit() else None

    parts = load_parts(mv, fname)
    # music21 解析整份总谱很慢（终乐章约一分钟），乐手标记和排练记号按谱面文件指纹缓存
    import hashlib
    cache = ROOT / "demo" / "cache" / f"m{mv}_marks.json"
    sig = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
    if cached and cached.get("sig") == sig:
        for p, mk in zip(parts, cached["marks"]):
            p["marks"] = mk
        reh_marks = [tuple(x) for x in cached["rehearsal"]]
    else:
        score = m21.converter.parse(str(src))
        for p, mp in zip(parts, score.parts):
            p["marks"] = player_marks(mp)
        reh_marks = sorted({(rm.getContextByClass("Measure").number, str(rm.content))
                            for rm in score.recurse().getElementsByClass(m21.expressions.RehearsalMark)
                            if rm.getContextByClass("Measure") is not None}, key=lambda x: x[0])
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"sig": sig, "marks": [p["marks"] for p in parts], "rehearsal": reh_marks},
                                    ensure_ascii=False), encoding="utf-8")

    stem = pathlib.Path(fname).stem
    st = json.loads((ROOT / "out" / f"{stem}_structure.json").read_text(encoding="utf-8"))
    first_m = st["measure_numbers"][0]
    # 排练记号从实际使用的谱面（修正副本优先）读，这样重编后的编号（PDF 版本）会生效
    reh = [[first_m, "开头"]] + [[mn, mk] for mn, mk in reh_marks if mn != first_m]
    data = {
        "movement": mv, "title": TITLES[mv], "svg": svg, "timemap": timemap, "parts": parts, "ctrl": ctrl,
        "measureN": meas_n, "rehearsal": reh, "firstMeasure": first_m, "lastMeasure": st["measure_numbers"][-1],
    }
    if not args.no_align:
        apply_alignment(data, mv, timemap, meas_n, args.first_row, args.last_row, args.anchor_row)
    # 解析卡：cards/m<N>.json（每张卡从小节 m 开始，到下一张卡开始为止）。时刻换成片段时间（相对 data["audio"]["offset"]）。
    # 注释标记 〔ref:id〕 按本乐章出现顺序编号，换成上标数字。
    cp = ROOT / "cards" / f"m{mv}.json"
    if cp.exists() and data.get("audio"):
        import re as _re
        cj = json.loads(cp.read_text(encoding="utf-8"))["cards"]
        rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
        first_t = {}
        for r in rows:
            first_t.setdefault(r["measure"], r["audio_sec"])
        end_t = rows[-1]["audio_sec"] + rows[-1]["audio_dur"]
        off = data["audio"]["offset"]
        # 全片统一的注释编号（tools/build_refs.py 生成；同一出处共用一个编号，2026-11-09 用户要求）。
        # 没有编号表时退回按本乐章出现顺序编号。
        nf = ROOT / "cards" / "refs_numbering.json"
        num = json.loads(nf.read_text(encoding="utf-8")) if nf.exists() else {}
        sup = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

        def mark(mo):
            num.setdefault(mo.group(1), len(num) + 1)
            return "⁠" + str(num[mo.group(1)]).translate(sup)   # 词连接符：角标不与前一个字分行
        refs_all = json.loads((ROOT / "references.json").read_text(encoding="utf-8"))["refs"]
        short = {a["id"]: a.get("short") or f'{a.get("author", "")}：{a.get("title", "")}' for a in refs_all}
        # 卡片定位到对齐表的行：pass=2 表示反复的第二遍（第一乐章呈示部反复）
        occ = {}
        for i, r in enumerate(rows):
            occ.setdefault(r["measure"], []).append(i)
        row_of = lambda c: occ[c["m"]][c.get("pass", 1) - 1]
        cards_out = []
        for k, c in enumerate(cj):
            k0 = row_of(c)
            k1 = row_of(cj[k + 1]) if k + 1 < len(cj) else len(rows)
            t1 = rows[k1]["audio_sec"] if k1 < len(rows) else end_t
            m1 = rows[k1 - 1]["measure"]
            paras, notes = [], []          # notes：本卡用到的参考资料（只记对应关系，播放页不显示；片尾统一列出）
            for x in c["body"].split("\n"):
                mo = _re.match(r"@(\d+) ", x)
                pt = round(rows[k0]["audio_sec"] - off, 3)
                x = x[mo.end():] if mo else x
                for rid in _re.findall(r"〔ref:([^〕]+)〕", x):
                    num.setdefault(rid, len(num) + 1)
                    if all(n["n"] != num[rid] for n in notes):
                        notes.append({"n": num[rid], "t": pt, "text": short.get(rid, rid)})
                # 段落：开头 "@N " 表示第 N 小节才淡入，否则随卡片出现
                paras.append({"t": pt, "text": _re.sub(r"〔ref:([^〕]+)〕", mark, x)})
            cards_out.append({"t0": round(rows[k0]["audio_sec"] - off, 3), "t1": round(t1 - off, 3), "m0": c["m"], "m1": m1,
                              "title": c["title"], "paras": paras, "notes": notes,
                              "focus": c.get("focus", []), "gloss": c.get("gloss", [])})
        data["cards"] = cards_out
    # 曲式（右下结构卡）：form/m<N>.json，两级（大段 sections、小段 subs）；没有就退回按排练记号
    fp = ROOT / "form" / f"m{mv}.json"
    if fp.exists():
        fj = json.loads(fp.read_text(encoding="utf-8"))
        data["form"] = {"note": fj.get("note", ""),
                        "sections": [{"label": x["label"], "from": x["from"], "to": x["to"],
                                      "subs": [{"label": y["label"], "from": y["from"], "to": y["to"]} for y in x.get("subs", [])]}
                                     for x in fj["sections"]]}
    # 动机标记：motifs.json 里本乐章的出现，把声部英文名换成序号
    mp = ROOT / "motifs.json"
    if mp.exists():
        mj = json.loads(mp.read_text(encoding="utf-8"))
        names = [p["en"].replace("&amp;", "&") for p in parts]
        inst = []
        for x in mj["instances"]:
            if x["movement"] != mv:
                continue
            # 有 index（同名多行时由 tools/build_motifs.py 选定的行号）就用它，否则按英文名取第一行
            spans = [{"part": s["index"] if s.get("index") is not None else names.index(s["name"]), "from": s["from"], "to": s["to"]}
                     for s in x["parts"] if s.get("index") is not None or s["name"] in names]
            if spans:
                inst.append({"motif": x["motif"], "label": x["label"], "status": x["status"],
                             "color": mj["motifs"][x["motif"]]["color"], "spans": spans})
        data["motifs"] = inst
        print(f"动机标记：本乐章 {len(inst)} 处")
    (out / "data.js").write_text("window.SCORE = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    (out / "score.svg").write_text(svg, encoding="utf-8")
    n_marks = sum(len(p["marks"]) for p in parts)
    print(f"第 {mv} 乐章，位置 {args.range}：SVG {len(svg)} 字符；时间表 {len(timemap)} 个事件；"
          f"声部 {len(parts)} 个（隐藏 {sum(p['hide'] for p in parts)}，合并 {sum(p['row'] != i for i, p in enumerate(parts))}）；"
          f"乐手标记 {n_marks} 条 → {out / 'data.js'}")


if __name__ == "__main__":
    main()
