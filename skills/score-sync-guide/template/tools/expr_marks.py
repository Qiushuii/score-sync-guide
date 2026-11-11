"""列出某乐章谱面上的表情、速度、性格类文字记号（MusicXML <words>）及所在声部（中文名），供写解析卡用。
技术性记号（arco、pizz.、a 2、弱音器、换乐器、喇叭口朝上之外的演奏法等）默认滤掉；--all 全列。
用的是校订副本（scores_fixed/，没有就用原件）。结果只作线索，进卡片前对谱。
用法：python -X utf8 tools/expr_marks.py <乐章> [--all]
"""
import json
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = re.compile(r"^(\.|\*\)?|pizz\.?|arco|a ?\d\.?|[\d\. ]+a \d\.?|unis\.?|get\.?|trem\.?|div\.?|in [A-H][a-z]*\.?|.*Dämpfer.*|.*[sS]chl[äa]gel.*"
                  r"|.*nimmt.*|.*muta.*|zu \d|\d\.?|[1-4]\. ?.*|solo|Solo|Tutti|tutti|offen.*|\(offen\)|gestopft\.?|gest\.|Basstuba|Tuba"
                  r"|\(♮\)|\(♭\)|♯|♮|♭|.*tacet.*|nicht teilen|Bässe|Contrafag\.?|te|a|\( \)|\(unis\.\)|Flag\.?|\(Resonanz\)|.*Harfe.*"
                  r"|.*Saite.*|Griffbrett|Mediator|gliss\.?|.*Revision.*|.*Flöte.*|Klar.*|Glockenspiel|sim\.?|simile|loco|8va.*|.*Ruthe.*)$")


def main():
    mv = int(sys.argv[1]); allm = "--all" in sys.argv
    man = {m["id"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["movements"]}
    src = ROOT / "scores_fixed" / man[mv]["score"]
    if not src.exists():
        src = ROOT / man[mv]["score"]
    z = zipfile.ZipFile(src)
    n = [x for x in z.namelist() if x.endswith(".xml") and "META" not in x][0]
    s = z.read(n).decode("utf-8")
    names = dict(re.findall(r'<score-part id="(P\d+)">\s*<part-name[^>]*>([^<]*)', s))
    lab = json.loads((ROOT / "labels.json").read_text(encoding="utf-8"))
    zh = {}
    lm = lab.get(str(mv)) or lab.get(f"m{mv}") or {}
    out = {}
    order = list(names)
    for pm in re.finditer(r'<part id="(P\d+)">(.*?)</part>', s, re.S):
        pid = pm.group(1)
        nm = names[pid].strip()
        for mm in re.finditer(r'<measure[^>]*number="([^"]+)"[^>]*>(.*?)</measure>', pm.group(2), re.S):
            for w in re.findall(r"<words[^>]*>([^<]*)</words>", mm.group(2)):
                w = " ".join(w.split())
                if not w or (not allm and SKIP.match(w)):
                    continue
                out.setdefault((mm.group(1), w), []).append(nm)
    def key(k):
        m = re.match(r"(\d+)", k[0])
        return (int(m.group(1)) if m else 0, k[0])
    for (m, w) in sorted(out, key=key):
        parts = out[(m, w)]
        print(f"{m:>5}  {w}  |  {', '.join(dict.fromkeys(parts))[:160]}")


if __name__ == "__main__":
    main()
