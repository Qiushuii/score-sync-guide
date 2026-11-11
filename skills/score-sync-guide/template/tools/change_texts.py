"""列出指定声部里所有文字记号（换乐器提示常写成文字），只读 mxl。
用法：venv/Scripts/python.exe -X utf8 tools/change_texts.py <mxl> <声部序号,逗号分隔>"""
import sys, warnings, re
import music21 as m21
warnings.filterwarnings("ignore")
score = m21.converter.parse(sys.argv[1])
parts = list(score.parts)
want = [int(x) for x in sys.argv[2].split(",")]
KEY = re.compile(r"\b(in\s+[A-Ha-h]|muta|nimmt|nehmen|wechsel|change|tr(om)?p|kl(ar)?|clar|a\s*2|solo|[1-8]\.)", re.I)
for i in want:
    p = parts[i]
    hits = []
    for el in p.recurse().getElementsByClass((m21.expressions.TextExpression, m21.instrument.Instrument)):
        mm = el.getContextByClass("Measure")
        mn = mm.number if mm is not None else "?"
        txt = el.content if hasattr(el, "content") else f"[乐器]{el.instrumentName}"
        if isinstance(el, m21.instrument.Instrument) or KEY.search(str(txt)):
            hits.append(f"m{mn}:{txt}")
    print(i, p.partName, "|", "  ".join(hits[:40]))
