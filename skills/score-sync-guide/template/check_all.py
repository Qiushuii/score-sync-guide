import glob, html
from music21 import converter, tempo, meter, expressions

files = sorted(glob.glob("*.mxl"))
out = open("report.txt", "w", encoding="utf-8-sig")

def log(*a):
    out.write(" ".join(str(x) for x in a) + "\n")
    out.flush()

summary = {}
for f in files:
    print("处理中:", f.encode("ascii", "replace").decode(), flush=True)
    log(f"\n===== {f} =====")
    s = converter.parse(f)
    names, counts = [], {}
    for i, p in enumerate(s.parts):
        name = html.unescape(str(p.partName))
        ms = list(p.getElementsByClass("Measure"))
        ins = p.getInstrument(returnDefault=False)
        tr = ins.transposition if ins is not None else None
        names.append(name)
        counts[i] = len(ms)
        log(f"  [{i:02d}] {name:28} 小节数={len(ms):4} 移调={tr}")
    log("  小节数一致:", len(set(counts.values())) == 1)

    p0 = s.parts[0]
    ms0 = list(p0.getElementsByClass("Measure"))
    log("  拍号变化:", [(t.measureNumber, t.ratioString)
        for t in p0.recurse().getElementsByClass(meter.TimeSignature)])
    marks = [(t.measureNumber, t.text or "", t.number)
             for t in p0.recurse().getElementsByClass(tempo.MetronomeMark)]
    log("  速度标记:", marks)
    log("  速度异常(>200 或 <20):", [m for m in marks if m[2] and (m[2] > 200 or m[2] < 20)])
    log("  排练记号数:", len(list(s.recurse().getElementsByClass(expressions.RehearsalMark))))

    log("  前 8 小节(编号/本小节拍号/时值/音符数):")
    for m in ms0[:8]:
        ts = m.timeSignature
        log("   ", m.number, ts.ratioString if ts else "-",
            m.duration.quarterLength, len(list(m.recurse().notes)))

    bad = []
    for p in s.parts:
        ts_now = None
        for m in p.getElementsByClass("Measure"):
            if m.timeSignature is not None:
                ts_now = m.timeSignature
            if ts_now is None or m.paddingLeft or m.paddingRight:
                continue
            if abs(m.duration.quarterLength - ts_now.barDuration.quarterLength) > 0.01:
                bad.append((html.unescape(str(p.partName)), m.number))
    log("  时值异常小节数:", len(bad), bad[:20])
    summary[f] = names

log("\n===== 跨文件一致性 =====")
if files:
    base = summary[files[0]]
    for f, names in summary.items():
        log(f, "声部列表与首个文件相同:", names == base)
out.close()
print("完成，请打开 report.txt")