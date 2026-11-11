"""解析卡与谱面数据交叉核对（2026-11-10，用户同意做一次系统核对）。

逐句找"第 N 小节""第 N–M 小节""第 N、M 小节"，以及句中的乐器名和记号说法，对照 mxl：
1. 乐器在这些小节里有没有音（没有音就可疑：可能说错了声部或小节）；
2. 记号说法（喇叭口朝上、弱音器、espr.、突出、阻塞音、拨奏、弓杆、琴马、滑音、Pesante……）在这些小节（±1）
   有没有出现；出现在哪些声部；句中点名的乐器是否在其中。
只报"可疑"，不改卡片；结果写 analysis/卡片核对报告.md，人工逐条判断。
用法：python -X utf8 tools/cards_xcheck.py [乐章 ...]
"""
import json
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 中文乐器名 → 声部名正则（英、德两套写法都有）；顺序：长的在前，避免"低音单簧管"被"单簧管"截走
INST = [
    ("第一小提琴", r"^(Violin 1|Erste Violinen)"), ("第二小提琴", r"^(Violin 2|Zweite Violinen)"),
    ("独奏小提琴", r"^(Solo Violin|Violin$)"), ("小提琴", r"^(Violin|Erste Violinen|Zweite Violinen)"),
    ("中提琴", r"^(Viola|Violen)"), ("大提琴", r"^Violoncell"), ("低音提琴", r"^(Contrabass|Bässe)"),
    ("低音单簧管", r"Bass ?[Cc]larinet|Bassklarinette"), ("小单簧管", r"^(E[♭b] Clarinet|Klarinette in D)"),
    ("单簧管", r"Clarinet|Klarinette"), ("低音巴松", r"^Contra(bassoon|fagott)"), ("巴松", r"^(Bassoon|Vier Fagotte)"),
    ("英国管", r"^Englis"), ("双簧管", r"^(Oboe|Vier Oboen)"), ("短笛", r"^(Piccolo|Kleine Flöte)"),
    ("长笛", r"^(Flute|Vier Flöten)"), ("圆号", r"^(F Horn|Horn in F|Acht Hörner)"),
    ("小号", r"Trumpet|Trompete"), ("长号", r"^(Trombone|Drei Posaunen|Bass-Posaune)"), ("大号", r"Tuba"),
    ("定音鼓", r"Timpani|Pauken"), ("竖琴", r"Harp|Harfe"), ("钢片琴", r"^Celesta"),
    ("牛铃", r"Cow|Herdenglocken"), ("钹", r"Cymbal|Becken"), ("大鼓", r"Bass Drum|Grosse Trommel"),
    ("小军鼓", r"Snare|Kleine Trommel"), ("大锤", r"^Hammer"), ("木琴", r"Xylophon"), ("钟琴", r"Glockenspiel"),
    ("大锣", r"Tam"), ("桦条鞭", r"^Rute"), ("三角铁", r"Triang"),
]
GROUP = {"铜管": r"Horn in F|F Horn|Hörner|Trumpet|Trompete|Trombone|Posaune|Tuba",
         "木管": r"Flute|Flöte|Piccolo|Oboe|Englis|Clarinet|Klarinette|Bassoon|Fagott",
         "弦乐": r"Violin|Viola|Violen|Violoncell|Contrabass|Bässe"}
# 记号说法 → <words>/记谱里的正则
MARK = [
    ("喇叭口", r"(?i)scha+l?l?tr|sch\. auf"), ("弱音器", r"(?i)d[äa]+m?p?f[ep]?[er]r?|sord"),
    ("espr", r"espr|ausdrucksvol"), ("有表情", r"espr|ausdrucksvol"), ("突出", r"hervor"),
    ("阻塞", r"gest|stopped"), ("拨奏", r"pizz"), ("弓杆", r"col legno"), ("琴马", r"Steg"),
    ("滑音", r"port|gliss"), ("Pesante", r"Pesante"), ("不要赶", r"(?i)nicht eilen"), ("不要拖", r"(?i)schleppen"),
    ("催促", r"(?i)dr[äa]ng"), ("海绵槌", r"Schwamm|Schw."), ("木槌", r"Holz"), ("跳弓", r"spring"),
]


def load(mv):
    man = {m["id"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["movements"]}
    src = ROOT / "scores_fixed" / man[mv]["score"]
    if not src.exists():
        src = ROOT / man[mv]["score"]
    z = zipfile.ZipFile(src)
    s = z.read([x for x in z.namelist() if x.endswith(".xml") and "META" not in x][0]).decode("utf-8")
    names = {pid: " ".join(n.replace("&amp;", "&").split()) for pid, n in re.findall(r'<score-part id="(P\d+)">\s*<part-name[^>]*>([^<]*)', s)}
    notes, words, dyns, keys, times = {}, {}, {}, {}, {}
    for pm in re.finditer(r'<part id="(P\d+)">(.*?)</part>', s, re.S):
        nm = names[pm.group(1)]
        for mm in re.finditer(r'<measure[^>]*number="([^"]+)"[^>]*>(.*?)</measure>', pm.group(2), re.S):
            m = re.match(r"\d+", mm.group(1))
            if not m:
                continue
            b = int(m.group(0)); body = mm.group(2)
            for dy in re.findall(r"<dynamics[^>]*>(.*?)</dynamics>", body, re.S):
                dyns.setdefault(b, set()).update(re.findall(r"<(\w+)\s*/>", dy) + re.findall(r"<other-dynamics>([^<]*)<", dy))
            if "<transpose>" in pm.group(2)[:pm.group(2).find("</attributes>") + 1]:
                pass
            elif (k := re.search(r"<fifths>(-?\d+)</fifths>", body)):
                keys.setdefault(b, []).append(int(k.group(1)))
            if (t := re.search(r"<time[^>]*>\s*<beats>(\d+)</beats>\s*<beat-type>(\d+)", body)):
                times[b] = f"{t.group(1)}/{t.group(2)}"
            if re.search(r"<note[ >](?:(?!</note>).)*?<(pitch|unpitched)>", body, re.S):
                notes.setdefault(b, set()).add(nm)
            txt = " ".join(re.findall(r"<words[^>]*>([^<]*)</words>", body)) + " " + " ".join(re.findall(r"<(stopped|tremolo)", body))
            if "<pizzicato" in body or "pizz" in txt:
                txt += " pizz"
            if txt.strip():
                words.setdefault(b, []).append((nm, txt))
    return notes, words, dyns, keys, times


def bars(sent):
    out = []
    for m in re.finditer(r"第 (\d+)(?:[–-](\d+)|((?:、\d+)+))? 小节", sent):
        a = int(m.group(1))
        if m.group(2):
            out += list(range(a, int(m.group(2)) + 1))
        else:
            out.append(a)
            if m.group(3):
                out += [int(x) for x in re.findall(r"\d+", m.group(3))]
    return out


def main():
    mvs = [int(x) for x in sys.argv[1:]] or [1, 2, 3, 4]
    rep = ["# 解析卡与谱面交叉核对报告\n", "> `tools/cards_xcheck.py` 生成。只列可疑之处，需人工判断；“无音”指该乐器在所说小节里一个音也没有。\n"]
    total = 0
    for mv in mvs:
        notes, words, dyns, keys, times = load(mv)
        kmode = {b: max(set(v), key=v.count) for b, v in keys.items()}
        cards = json.loads((ROOT / "cards" / f"m{mv}.json").read_text(encoding="utf-8"))["cards"]
        rep.append(f"\n## 第 {mv} 乐章\n")
        for c in cards:
            for sent in re.split(r"(?<=[。！？])", c["body"].replace("\n", "")):
                bs = bars(sent)
                if not bs:
                    continue
                txt = re.sub(r"〔ref:[^〕]*〕", "", sent)
                insts, rest = [], txt
                for zh, rx in INST:
                    if zh in rest:
                        insts.append((zh, rx)); rest = rest.replace(zh, "＊")
                for g, rx in GROUP.items():
                    if g in txt:
                        insts.append((g, rx))
                issues = []
                span = [b for b in bs if b in notes or b in words]
                # 1. 点名的乐器在这些小节有没有音（整段范围只要有一个小节有音就算）
                rng = set(bs) | {b - 1 for b in bs} | {b + 1 for b in bs}
                for zh, rx in insts:
                    if not any(re.search(rx, p) for b in rng for p in notes.get(b, ())):
                        issues.append(f"{zh}在第 {bs[0]}{'–' + str(bs[-1]) if len(bs) > 1 else ''} 小节没有音")
                # 2. 记号说法
                for key, rx in MARK:
                    if key not in txt:
                        continue
                    near = set()
                    for b in bs:
                        for bb in (b - 1, b, b + 1):
                            near |= {p for p, w in words.get(bb, []) if re.search(rx, w)}
                    if not near:
                        issues.append(f"“{key}”在所说小节（±1）的谱面文字里找不到")
                    elif insts and not any(re.search(r, p) for _, r in insts for p in near):
                        issues.append(f"“{key}”实际写在：{'、'.join(sorted(near))[:120]}")
                # 3. 力度记号（句中出现 pp、fff 之类，且同句有小节号）：所说小节 ±1 有没有
                near = set(bs) | {b - 1 for b in bs} | {b + 1 for b in bs}
                for dm in set(re.findall(r"(?<![A-Za-z])(ppp|pp|mp|mf|fff|ff|sff|sfz|sf|ffp)(?![A-Za-z])", txt)):
                    if not any(dm in dyns.get(b, ()) for b in near):
                        issues.append(f"力度 {dm} 在所说小节（±1）找不到")
                # 4. 调号说法
                CN = dict(zip("一二三四五六七", range(1, 8)))
                for km in re.finditer(r"调号(?:换成|回到|变成|改成|转成)?([一二三四五六七])个(升|降)号|调号(清空|回到无升降)", txt):
                    want = 0 if km.group(3) else CN[km.group(1)] * (1 if km.group(2) == "升" else -1)
                    if not any(kmode.get(b) == want for b in near):
                        issues.append(f"调号（{want:+d}）在所说小节（±1）没有出现，实际：" + "、".join(f"{b}:{kmode[b]:+d}" for b in sorted(near) if b in kmode))
                # 5. 拍号说法
                for tm in re.finditer(r"(\d+)/(\d+) 拍|改回?(二|三|四)拍子|(两|二|三|四)拍的小节", txt):
                    if tm.group(1):
                        want = {f"{tm.group(1)}/{tm.group(2)}"}
                    else:
                        n = {"二": 2, "两": 2, "三": 3, "四": 4}[tm.group(3) or tm.group(4)]
                        want = {f"{n}/4", f"{n}/2", f"{n}/8"} if tm.group(4) else {f"{n}/4", f"{n}/2", f"{2*n}/8", f"{n * 2}/4" if n == 2 else ""}
                    if not any(times.get(b) in want for b in near):
                        issues.append(f"拍号（{'/'.join(sorted(x for x in want if x))}）在所说小节（±1）没有变化，实际：" + "、".join(f"{b}:{times[b]}" for b in sorted(near) if b in times))
                # 6. 声部数（只查单个小节的说法）
                for nm in re.finditer(r"(\d+) 个声部", txt):
                    if len(bs) == 1 or len(set(bs)) == 1:
                        have = len(notes.get(bs[0], ()))
                        if abs(have - int(nm.group(1))) > max(2, 0.15 * have):
                            issues.append(f"说 {nm.group(1)} 个声部，第 {bs[0]} 小节实际 {have} 个声部有音")
                if issues:
                    total += 1
                    rep.append(f"- 卡 {c['m']}{'（第二遍）' if c.get('pass') == 2 else ''}：{txt.strip()[:160]}\n  - " + "\n  - ".join(issues))
    out = ROOT / "analysis" / "卡片核对报告.md"
    out.write_text("\n".join(rep) + "\n", encoding="utf-8")
    print(f"可疑句子 {total} 条 → {out}")


if __name__ == "__main__":
    main()
