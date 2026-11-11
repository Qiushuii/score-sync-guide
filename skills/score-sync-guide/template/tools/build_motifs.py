"""生成 motifs.json：全片要标注的动机（用户 2026-11-07 定：约 9 个，分两层）及每次出现的位置和声部。

位置来源：tools/motif_scan.py 的旋律扫描（音程 + 节奏比例）、格言动机的 12 根音和弦扫描、Monahan 配套图表的标注。
每条 instance 写明 status：
  "已核对（…）"：谱面数据确认了位置（可进成片）；
  "候选（…）"：只有文献说法或和声相似度、没能用旋律确认，播放器检查版可显示，成片不用。
声部名用 out/*_structure.json 的英文名（build_score.py 按它换成序号；同名多行时取第一行）。
用法：python -X utf8 tools/build_motifs.py   （会覆盖 motifs.json，旧版先备份）
"""
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from similar_passages import notes  # noqa: E402
from motif_scan import parts_en  # noqa: E402

TODAY = "2026-11-07"
MOTIFS = {
    "motto": {"name_zh": "格言动机", "layer": 1, "color": "#c9a227",
              "desc": "大三和弦转同根小三和弦，下面是定音鼓的特征节奏；全曲的标志",
              "sources": ["wiki-m6", "jax-notes", "monahan-2015-companion"]},
    "march": {"name_zh": "进行曲主题", "layer": 1, "color": "#8e6cc0",
              "desc": "第一乐章第一主题：八度大跳的进行曲旋律；再现部先以 A 大调回来",
              "sources": ["wiki-m6"]},
    "second": {"name_zh": "第二主题（\"阿尔玛主题\"）", "layer": 1, "color": "#4f86c6",
               "desc": "第一乐章第二主题，F 大调，Schwungvoll；尾声以 A 大调凯旋。\"阿尔玛主题\"之名据阿尔玛回忆，见 claims.alma-theme",
               "sources": ["cooke-zander", "monahan-2011", "monahan-2015-companion"]},
    "chorale1": {"name_zh": "众赞歌", "layer": 1, "color": "#5aa469",
                 "desc": "第一乐章过渡段的木管众赞歌；再现部节奏紧缩（时值减半）并加钢片琴",
                 "sources": ["usuo-guide", "monahan-2015-companion"]},
    "hammer": {"name_zh": "众赞歌 → 大锤主题", "layer": 1, "color": "#2f7d6d",
               "desc": "终乐章第 49 小节的 C 小调众赞歌；Monahan 指出它变形为过渡主题和大锤落下时的主题（图 7.5）",
               "sources": ["monahan-2015-companion", "wiki-m6"]},
    "andante": {"name_zh": "行板主题", "layer": 2, "color": "#3f9fa6",
                "desc": "行板的主题，降 E 大调，弱起 E♭–F–G", "sources": []},
    "trio": {"name_zh": "中段主题（\"老派地\"）", "layer": 2, "color": "#a98ad6",
             "desc": "谐谑曲中段主题，标 Altväterisch，拍号几乎每小节切换", "sources": ["wiki-m6"]},
    "intro4": {"name_zh": "终乐章引子", "layer": 2, "color": "#9c7a54",
               "desc": "终乐章引子群：竖琴、钢片琴的和弦与小提琴高飞的旋律；在再现部、尾声开头回来（Monahan：每一大块都从引子群开始）",
               "sources": ["monahan-2015-companion"]},
    "p4": {"name_zh": "终乐章第一主题", "layer": 2, "color": "#6b8e23",
           "desc": "终乐章呈示部第一主题（Allegro energico），A 小调进行曲", "sources": ["monahan-2015-companion"]},
}


def part_index(mv, name, a, b):
    """同名的行可能有好几条（如终乐章 6 行 Sechs Trompeten）：取第 a–b 小节里音最多的那一行的序号。"""
    en, _ = parts_en(mv)
    idx = [i for i, n in enumerate(en) if n == name]
    if len(idx) <= 1:
        return idx[0] if idx else None
    d = notes(mv)
    N = d["numbers"]
    ia, ib = N.index(str(a)), N.index(str(b))
    cnt = {i: 0 for i in idx}
    for pos, off, dur, midi, pi in d["notes"]:
        if pi in cnt and ia <= pos <= ib:
            cnt[pi] += 1
    return max(idx, key=lambda i: cnt[i])


def inst(motif, mv, label, parts, status, evidence):
    return {"motif": motif, "movement": mv, "label": label,
            "parts": [{"name": n, "index": part_index(mv, n, a, b), "from": a, "to": b} for n, a, b in parts],
            "status": status, "evidence": evidence, "date": TODAY}


def chord_parts(mv, a, b, pcs, timp_from=None):
    """第 a–b 小节里，奏的音全落在 pcs 里的声部（英文名，去重），加定音鼓。"""
    d = notes(mv)
    en, _ = parts_en(mv)
    N = d["numbers"]
    ia, ib = N.index(str(a)), N.index(str(b))
    by = {}
    for pos, off, dur, midi, pi in d["notes"]:
        if ia <= pos <= ib:
            by.setdefault(pi, set()).add(midi % 12)
    names = []
    for pi, s in sorted(by.items()):
        if s <= pcs and len(s) >= 1 and en[pi] not in names and "Pauk" not in en[pi] and "Timp" not in en[pi]:
            names.append(en[pi])
    out = [(n, a, b) for n in names]
    timp = next(n for n in en if "Pauk" in n or "Timp" in n)
    out.append((timp, timp_from or a, b))
    return out


def main():
    old = ROOT / "motifs.json"
    if old.exists():
        shutil.copy(old, ROOT / f"motifs.json.bak-{TODAY.replace('-', '')}")
    prev = json.loads(old.read_text(encoding="utf-8"))
    I = [x for x in prev["instances"] if x["motif"] == "motto" and x["movement"] == 1]   # 第一乐章格言：已对原谱核实，原样保留

    A = lambda r: {r % 12, (r + 3) % 12, (r + 4) % 12, (r + 7) % 12}
    for a, b, r, nm in ((10, 11, 9, "A"), (65, 66, 7, "G"), (96, 97, 0, "C"), (395, 396, 0, "C"), (784, 785, 9, "A")):
        I.append(inst("motto", 4, f"格言动机：{nm} 大 → {nm} 小", chord_parts(4, a, b, A(r), a - 1),
                      "已核对（谱面数据 + Monahan 标注）",
                      f"12 根音扫描：第 {a}→{b} 小节干净的 {nm} 大三和弦转 {nm} 小三和弦，附近有定音鼓起音；Monahan 终乐章缩谱在此标 MOTTO。"
                      + ("第 784 小节一带是删去的第三击大锤原位置（Monahan：大锤唯一一次与格言同时）。" if a == 784 else "")))
    I.append(inst("motto", 4, "最后的格言：只剩 A 小三和弦", chord_parts(4, 818, 820, {9, 0, 4}, 816),
                  "候选（文献：Wikipedia、Monahan\"最后的格言：大三和弦被省去\"；谱面上 A 小三和弦与定音鼓待逐声部看谱）",
                  "Monahan 缩谱第 41 页 FINAL MOTTO；Wikipedia：终乐章结尾同一节奏、上方只剩 A 小三和弦。"))

    I += [
        inst("march", 1, "进行曲主题", [("Violin 1", 6, 13), ("Violin 2", 6, 13)], "已核对（旋律扫描）", "motif_scan：第一小提琴第 6–8 小节原型。"),
        inst("march", 1, "进行曲主题（变形）", [("Oboe 1 - 2", 148, 151), ("B♭ Clarinet 1", 148, 151)], "已核对（旋律扫描，容差 2 个音程）", "motif_scan --octave --tol 2：移高 7 个半音。"),
        inst("march", 1, "进行曲主题：再现，先是 A 大调", [("Violin 1", 290, 297), ("Oboe 1 - 2", 290, 293), ("B♭ Clarinet 1", 290, 293)],
             "已核对（旋律扫描 + 逐音查看：第 290 小节含 C♯，第 294 小节转回小调）", "motif_scan 容差 2（大调使音程差一个半音）；Monahan：再现部从第 286 小节开始。"),
        inst("march", 1, "进行曲主题（变形）", [("Violin 1", 326, 329), ("Violin 2", 326, 329), ("Violoncello", 326, 329)], "已核对（旋律扫描，容差 2 个音程）", "motif_scan --octave --tol 2。"),

        inst("second", 1, "第二主题（\"阿尔玛主题\"）", [("Violin 1", 77, 84), ("Violin 2", 77, 84)], "已核对（旋律扫描）", "motif_scan：原型。"),
        inst("second", 1, "第二主题", [("Violin 1", 85, 92), ("Violin 2", 85, 92)], "已核对（旋律扫描）", "motif_scan：原调重复。"),
        inst("second", 1, "第二主题（木管）", [("Flute 1 - 2", 99, 106), ("Oboe 1 - 2", 99, 106), ("E♭ Clarinet", 99, 106), ("B♭ Clarinet 1", 99, 106)], "已核对（旋律扫描）", "motif_scan：原调，木管。"),
        inst("second", 1, "第二主题：再现，D 大调", [("Violin 1", 371, 378), ("Violin 2", 371, 378)], "已核对（旋律扫描，差 1 个音程、节奏有变）", "motif_scan：移低 15 个半音（即 D 大调）。"),
        inst("second", 1, "第二主题：A 大调凯旋", [("F Trumpet 1", 450, 462), ("Flute 1 - 2", 453, 457), ("Oboe 1 - 2", 453, 457), ("C Clarinet", 453, 457), ("Viola", 453, 458)],
             "已核对（旋律扫描，差 1 个音程）", "motif_scan：移低 8 个半音（即 A 大调）；Serotsky、Monahan：尾声以第二主题的凯旋陈述结束，S 到达主调大调。"),
        inst("second", 1, "第二主题（低音乐器）", [("Trombone 1", 419, 424), ("Tuba", 419, 424)], "候选（旋律扫描差 1 个音程，在低音铜管上，听感上是否认得出待听）", "motif_scan。"),

        inst("chorale1", 1, "众赞歌", [("Flute 1 - 2", 61, 76), ("Oboe 1 - 2", 61, 76), ("B♭ Clarinet 1", 61, 76), ("Bassoon 1", 61, 76)], "已核对（旋律扫描）", "motif_scan：长笛、双簧管原型；单簧管、巴松为低声部。标 Stets das gleiche Tempo。"),
        inst("chorale1", 1, "众赞歌：节奏紧缩 + 钢片琴", [("Flute 1 - 2", 340, 351), ("Celesta", 340, 351), ("Viola", 340, 351)], "已核对（旋律扫描 + 时值比较：二分音符变四分音符）", "motif_scan；文献：USUO 导赏\"速度加倍、加钢片琴\"。"),

        inst("hammer", 4, "众赞歌（C 小调）", [("Vier Fagotte", 49, 64), ("Contrafagott", 49, 64), ("Bassklarinette in B", 49, 64), ("Acht Hörner in F", 53, 64), ("Bass Tuba in F", 53, 64)],
             "已核对（位置：谱面 Schwer. Marcato、调号；旋律在别处找不到，与 Newlin 说法一致）", "Monahan 图 7.5：众赞歌 mm. 49–64。"),
        inst("hammer", 4, "大锤主题（第一击）", [("Sechs Trompeten", 336, 350)], "已核对（旋律扫描：第 336、348 小节小号）", "Monahan：大锤主题源自第 49 小节众赞歌。"),
        inst("hammer", 4, "大锤主题（第二击）", [("Sechs Trompeten", 479, 491)], "已核对（旋律扫描：第 479 小节与第 336 小节原调相同，第 489 小节再现）", "motif_scan。"),

        inst("andante", 2, "行板主题", [("Violin 1", 0, 9)], "已核对（旋律扫描）", "motif_scan：原型。"),
        inst("andante", 2, "行板主题（木管）", [("Oboe 1", 99, 106), ("Bb Clarinet 1", 99, 106)], "已核对（旋律扫描）", "motif_scan：原调。"),
        inst("andante", 2, "行板主题再现", [("Violin 1", 28, 35)], "候选（和声相似度 0.80，旋律未能自动匹配）", "similar_passages。"),
        inst("andante", 2, "行板主题再现", [("Violin 1", 173, 180)], "候选（调号回到降 E 大调，旋律未能自动匹配）", "score_facts。"),

        inst("trio", 3, "中段主题（\"老派地\"）", [("Oboe 1", 98, 105)], "已核对（旋律扫描）", "motif_scan：原型。"),
        inst("trio", 3, "中段主题（第二次中段）", [("Oboe 1", 275, 282)], "已核对（旋律扫描：移高 9 个半音；谱面写'像第一次那样'）", "motif_scan。"),

        inst("intro4", 4, "引子", [("Erste Violinen", 1, 8), ("Celesta", 1, 1), ("2 Harfen", 1, 8)], "已核对（旋律扫描）", "motif_scan：第一小提琴第 3–6 小节原型。"),
        inst("intro4", 4, "引子回来（再现部块开头）", [("Erste Violinen", 520, 529)], "已核对（旋律扫描：第 524 小节原调）", "Monahan：再现部块从引子群开始，\"逐小节对应第 17 小节起\"。"),
        inst("intro4", 4, "引子回来（尾声开头）", [("Erste Violinen", 773, 781)], "已核对（旋律扫描：第 777 小节移低 3 个半音）", "Monahan：尾声从引子群开始。"),
        inst("intro4", 4, "引子回来（展开部块开头，与 S2 混合）", [("Erste Violinen", 229, 238)], "候选（Monahan：\"减弱的引子群\"，旋律未能自动匹配）", "Monahan 缩谱第 11 页。"),

        inst("p4", 4, "第一主题", [("Vier Oboen", 114, 121), ("Erste Violinen", 114, 121), ("Zweite Violinen", 114, 121)], "已核对（旋律扫描）", "motif_scan：原型。"),
        inst("p4", 4, "第一主题：再现", [("Erste Violinen", 642, 649), ("Zweite Violinen", 642, 649)], "候选（和声相似度 0.76、Monahan 说逐小节对应呈示部；本 mxl 的旋律线未能自动匹配，待对原谱）", "similar_passages、Monahan。"),
    ]

    # 声部名检查：每个 name 必须在对应乐章的 structure 里
    bad = []
    for x in I:
        en, _ = parts_en(x["movement"])
        for p in x["parts"]:
            if p["name"] not in en:
                bad.append((x["movement"], x["label"], p["name"]))
    if bad:
        sys.exit("声部名不在谱里：" + json.dumps(bad, ensure_ascii=False))
    out = {"_说明": prev["_说明"] + " layer：1=贯穿全曲（始终标注），2=只在本乐章标注。由 tools/build_motifs.py 生成，改动请改脚本。",
           "motifs": MOTIFS, "instances": I}
    old.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    st = Counter(("已核对" if x["status"].startswith("已核对") else "候选") for x in I)
    print(f"写入 motifs.json：{len(MOTIFS)} 个动机，{len(I)} 处出现（{dict(st)}）")


if __name__ == "__main__":
    main()
