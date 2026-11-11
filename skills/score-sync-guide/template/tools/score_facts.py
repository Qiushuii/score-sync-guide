"""从谱面（有校订副本就用副本）抽出写导赏要用的事实，只读，不改谱。
输出 out/m<N>_facts.json 和 out/m<N>_facts.txt（人读版）：
  - 每小节：调号（取不移调声部的多数）、拍号变化、文字记号（速度/表情，去重后列出所在声部数）、排练记号、
    最强/最弱力度、在奏声部数、音符数、对齐后的录音时刻（第一遍）。
  - 文字记号汇总：同一文字在同一小节出现在几个声部。
用法：python -X utf8 tools/score_facts.py 1 [2 3 4]
"""
import collections
import json
import pathlib
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DYN_ORDER = ["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff"]
FIFTHS = {-7: "降C", -6: "降G", -5: "降D", -4: "降A", -3: "降E", -2: "降B", -1: "F", 0: "C", 1: "G", 2: "D", 3: "A",
          4: "E", 5: "B", 6: "升F", 7: "升C"}


def load_xml(path: pathlib.Path) -> ET.Element:
    with zipfile.ZipFile(path) as z:
        cont = ET.fromstring(z.read("META-INF/container.xml"))
        full = next(e.get("full-path") for e in cont.iter() if e.tag.endswith("rootfile"))
        return ET.fromstring(z.read(full))


def main() -> None:
    man = {m["id"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["movements"]}
    labels = json.loads((ROOT / "labels.json").read_text(encoding="utf-8")) if (ROOT / "labels.json").exists() else {}
    for mv in (int(x) for x in sys.argv[1:]):
        src = ROOT / "scores_fixed" / man[mv]["score"]
        if not src.exists():
            src = ROOT / man[mv]["score"]
        root = load_xml(src)
        names = {sp.get("id"): (sp.findtext("part-name") or "").strip() for sp in root.iter("score-part")}
        st = json.loads((ROOT / man[mv]["structure"]).read_text(encoding="utf-8"))
        zh = {p.get("id", p.get("name")): p.get("zh_name") for p in st["parts"]} if st.get("parts") else {}
        rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
        t_audio = {}
        for r in rows:                                   # 第一遍的录音时刻
            t_audio.setdefault(r["position"], r["audio_sec"])

        per = collections.defaultdict(lambda: {"keys": collections.Counter(), "time": None, "words": collections.Counter(),
                                               "reh": None, "dyn": [], "parts": set(), "notes": 0, "number": None,
                                               "tempo": None})
        for part in root.iter("part"):
            pid = part.get("id")
            transposing = False
            for pos, meas in enumerate(part.findall("measure")):
                d = per[pos]
                d["number"] = meas.get("number")
                for at in meas.iter("attributes"):
                    if at.find("transpose") is not None:
                        transposing = True
                    k = at.find("key")
                    if k is not None and k.findtext("fifths") is not None and not transposing:
                        d["keys"][(int(k.findtext("fifths")), k.findtext("mode") or "")] += 1
                    t = at.find("time")
                    if t is not None and t.findtext("beats"):
                        d["time"] = f"{t.findtext('beats')}/{t.findtext('beat-type')}"
                for dr in meas.iter("direction"):
                    for w in dr.iter("words"):
                        txt = " ".join((w.text or "").split())
                        if txt and txt != "other-dynamics":
                            d["words"][txt] += 1
                    for r in dr.iter("rehearsal"):
                        d["reh"] = (r.text or "").strip()
                    for dy in dr.iter("dynamics"):
                        for c in dy:
                            tag = c.tag
                            if tag in DYN_ORDER:
                                d["dyn"].append(tag)
                    snd = dr.find("sound")
                    if snd is not None and snd.get("tempo"):
                        d["tempo"] = snd.get("tempo")
                for n in meas.iter("note"):
                    if n.find("rest") is None and n.find("chord") is None:
                        d["notes"] += 1
                        d["parts"].add(pid)
                    for dy in n.iter("dynamics"):
                        for c in dy:
                            if c.tag in DYN_ORDER:
                                d["dyn"].append(c.tag)

        out, lines = [], []
        cur_key = None
        for pos in sorted(per):
            d = per[pos]
            key = d["keys"].most_common(1)[0][0] if d["keys"] else None
            key_change = key if key and key != cur_key else None
            if key:
                cur_key = key
            dyn = sorted(set(d["dyn"]), key=DYN_ORDER.index)
            rec = {"position": pos, "measure": d["number"], "audio_sec": t_audio.get(pos),
                   "key_change": ({"fifths": key_change[0], "name": FIFTHS.get(key_change[0]) + "（调号）", "mode": key_change[1]}
                                  if key_change else None),
                   "time": d["time"], "rehearsal": d["reh"], "tempo_bpm": d["tempo"],
                   "words": [{"text": w, "n_parts": c} for w, c in d["words"].most_common()],
                   "dyn_min": dyn[0] if dyn else None, "dyn_max": dyn[-1] if dyn else None,
                   "n_parts": len(d["parts"]), "n_notes": d["notes"]}
            out.append(rec)
            flag = []
            if rec["rehearsal"]:
                flag.append(f"[{rec['rehearsal']}]")
            if key_change:
                flag.append(f"调号 {FIFTHS.get(key_change[0])}({key_change[0]:+d})")
            if rec["time"]:
                flag.append(f"拍号 {rec['time']}")
            ws = [w for w in rec["words"] if not w["text"].replace(".", "").replace(" ", "").isdigit()]
            if ws:
                flag.append(" | ".join(f"{w['text']}×{w['n_parts']}" for w in ws[:6]))
            t = rec["audio_sec"]
            ts = f"{int(t // 60)}:{t % 60:04.1f}" if t is not None else "—"
            lines.append(f"{rec['measure']:>4} {ts:>7}  声部{rec['n_parts']:>2} 音{rec['n_notes']:>4} "
                         f"{(rec['dyn_min'] or '') + '–' + (rec['dyn_max'] or '') if rec['dyn_max'] else '':<9} " + "  ".join(flag))
        (ROOT / "out" / f"m{mv}_facts.json").write_text(json.dumps({"source": str(src.relative_to(ROOT)), "measures": out},
                                                                    ensure_ascii=False, indent=1), encoding="utf-8")
        (ROOT / "out" / f"m{mv}_facts.txt").write_text(
            f"第 {mv} 乐章谱面事实（tools/score_facts.py 生成；来源 {src.relative_to(ROOT)}）\n"
            "列：小节号 录音时刻（第一遍） 在奏声部数 音符数 力度范围 [排练记号] 调号/拍号变化 文字记号×出现声部数\n\n"
            + "\n".join(lines) + "\n", encoding="utf-8")
        print(f"第 {mv} 乐章：{len(out)} 小节 → out/m{mv}_facts.txt")


if __name__ == "__main__":
    main()
