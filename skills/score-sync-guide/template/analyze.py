# -*- coding: utf-8 -*-
"""
乐谱体检 + 数据导出（每个文件只解析一次）

用法：把本文件和 .mxl 放在同一个文件夹，双击 run.bat。
输出：
  report.txt            体检报告（UTF-8 带 BOM，记事本可直接打开）
  out/<文件名>_structure.json   小节编号、反复结构、演奏顺序、排练记号、
                                速度标记、各声部活跃区间（做视频用的数据）
  out/<文件名>_parts.csv        声部表（自动生成中文名 zh_name 与分组 group，可手改）
  names_override.csv            可选：列 name,zh_name,group，手工指定的中文名优先于自动规则
"""
import csv
import glob
import html
import json
import os
import re
import sys
import time
import traceback
from collections import Counter, defaultdict

from music21 import bar, converter, expressions, meter, spanner, tempo

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
os.makedirs("out", exist_ok=True)

GAP = 4  # 休止不超过这么多小节，视为声部仍在场
REPORT = open("report.txt", "w", encoding="utf-8-sig")


def log(*a):
    REPORT.write(" ".join(str(x) for x in a) + "\n")
    REPORT.flush()


def say(msg):
    """控制台只输出 ASCII，避免 Windows 控制台编码报错。"""
    print(str(msg).encode("ascii", "replace").decode(), flush=True)


def clean(name):
    return html.unescape(" ".join(str(name).split()))


# ---------------------------------------------------------------- 中文声部名
# (正则, 中文名, 分组)；按顺序匹配，先具体后一般。匹配前名称统一为小写，♭ 换成 b。
RULES = [
    (r"piccolo|kleine fl", "短笛", "木管"),
    (r"fl(ute|öte|oete|oten|öten)", "长笛", "木管"),
    (r"oboe", "双簧管", "木管"),
    (r"english horn|englisch horn|cor anglais", "英国管", "木管"),
    (r"klarinette in d|d clarinet", "D调小单簧管", "木管"),
    (r"eb clarinet|klarinette in es", "降E调小单簧管", "木管"),
    (r"c clarinet", "C调单簧管", "木管"),
    (r"a bass clarinet|bassklarinette in a", "A调低音单簧管", "木管"),
    (r"bass clarinet|bassklarinette", "低音单簧管", "木管"),
    (r"a clarinet|klarinette in a", "A调单簧管", "木管"),
    (r"bb clarinet|klarinetten? in b|clarinet", "降B调单簧管", "木管"),
    (r"contrabassoon|contrafagott|kontrafagott", "低音巴松", "木管"),
    (r"bassoon|fagott", "巴松", "木管"),
    (r"horn in f|f horn|hörner|horner|^horn", "圆号", "铜管"),
    (r"f trumpet", "F调小号", "铜管"),
    (r"bb trumpet", "降B调小号", "铜管"),
    (r"trumpet|trompet", "小号", "铜管"),
    (r"bass-posaune.*tuba|bass trombone.*tuba", "低音长号/大号", "铜管"),
    (r"trombone|posaune", "长号", "铜管"),
    (r"tuba", "大号", "铜管"),
    (r"timpani|pauken", "定音鼓", "打击乐"),
    (r"glockenspiel", "钟琴", "打击乐"),
    (r"xylophone", "木琴", "打击乐"),
    (r"tubular bells", "管钟", "打击乐"),
    (r"herdenglocken|glockengel", "牧牛铃与低沉钟声", "打击乐"),
    (r"cow ?bell", "牛铃", "打击乐"),
    (r"^bells?$", "钟", "打击乐"),
    (r"bass drum|grosse trommel|große trommel", "大鼓", "打击乐"),
    (r"snare drum|kleine trommel", "小军鼓", "打击乐"),
    (r"triangle|triangel", "三角铁", "打击乐"),
    (r"cymbal|becken", "钹", "打击乐"),
    (r"tam-?tam", "大锣", "打击乐"),
    (r"wood ?block", "木块", "打击乐"),
    (r"castanet", "响板", "打击乐"),
    (r"^rute", "桦条鞭（Rute）", "打击乐"),
    (r"^hammer", "木槌（Hammer）", "打击乐"),
    (r"harp|harfe", "竖琴", "竖琴与键盘"),
    (r"celesta", "钢片琴", "竖琴与键盘"),
    (r"solo violin", "独奏小提琴", "弦乐"),
    (r"violin 1|erste violin|1st violin", "第一小提琴", "弦乐"),
    (r"violin 2|zweite violin|2nd violin", "第二小提琴", "弦乐"),
    (r"^violin$", "小提琴", "弦乐"),
    (r"viola|violen|bratsche", "中提琴", "弦乐"),
    (r"violoncell|cello", "大提琴", "弦乐"),
    (r"contrabass|kontrabass|bässe|basse|double bass", "低音提琴", "弦乐"),
]
NUMWORDS = r"^(?:zwei|drei|vier|fünf|sechs|sieben|acht)\s+"


def load_overrides():
    """可选文件 names_override.csv（列：name,zh_name,group）：手工指定的中文名优先于规则。"""
    ov = {}
    if os.path.exists("names_override.csv"):
        with open("names_override.csv", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                nm = clean(row.get("name", ""))
                if nm:
                    ov[nm] = (row.get("zh_name", "").strip(), row.get("group", "").strip())
    return ov


OVERRIDES = load_overrides()


def translate(name):
    """返回 (中文名, 分组, 备注)。"""
    n = clean(name)
    if n in OVERRIDES and OVERRIDES[n][0]:
        return OVERRIDES[n][0], OVERRIDES[n][1] or "未分类", "手工指定"
    low = n.replace("♭", "b").replace("♯", "#").lower()
    hit = None
    for pat, z, g in RULES:
        mt = re.search(pat, low)
        if mt:
            hit = (mt, z, g)
            break
    if hit is None:
        return n, "未分类", "未识别，请手工指定"
    mt, zh, grp = hit
    note = ""
    # 去掉已匹配部分与“2 Paar / Vier / in F”这类人数、调性，再取编号
    prefix = low[:mt.start()].strip()
    if re.fullmatch(r"(\d+(\s+paar)?|zwei|drei|vier|fünf|sechs|sieben|acht)", prefix):
        prefix = ""  # “2 Harps / 2 Paar Pauken / Vier Flöten”里的人数不是编号
    rest = prefix + " " + low[mt.end():]
    rest = re.sub(r"\bin [a-z]b?\b", "", rest)
    nums = re.findall(r"(?<![a-z])\d+(?![a-z])", rest)
    if nums:
        zh = f"{zh} {'·'.join(nums)}"
    if re.search(r"divided|geteilt|div\.", low):
        zh += "（三部分）" if "dreifach" in low else "（分部）"
    if low == "violin":
        note = "单独的 Violin 声部，可能是独奏小提琴，请核对"
    return zh, grp, note


# ---------------------------------------------------------------- 反复结构
def read_repeats(measures, part):
    """返回 (starts, ends, volta)；全部用“小节在声部中的位置”(0 起算)表示。"""
    starts, ends, volta = set(), set(), {}
    for i, m in enumerate(measures):
        for b in (m.leftBarline, m.rightBarline):
            if isinstance(b, bar.Repeat):
                if b.direction == "start":
                    starts.add(i)
                elif b.direction == "end":
                    ends.add(i)
    index_of = {id(m): i for i, m in enumerate(measures)}
    for rb in part.recurse().getElementsByClass(spanner.RepeatBracket):
        nums = getattr(rb, "numberRange", None)
        if not nums:
            try:
                nums = [int(x) for x in str(rb.number).replace(" ", "").split(",") if x]
            except ValueError:
                nums = []
        idxs = [index_of[id(el)] for el in rb.getSpannedElements() if id(el) in index_of]
        if idxs and nums:
            # MusicXML 读入后房子往往只记着首尾两个小节，这里把中间的小节补全
            for i in range(min(idxs), max(idxs) + 1):
                volta.setdefault(i, set()).update(nums)
    return starts, ends, volta


def play_order(n, starts, ends, volta):
    """按反复记号与房子(volta)展开，返回实际演奏顺序（小节位置列表）。"""
    order = []
    i, start, cur_pass = 0, 0, 1
    jumped = set()
    in_volta_flag = False
    guard = 0
    while i < n and guard < 20 * n + 100:
        guard += 1
        if i in starts:
            start = i
        if i not in volta and in_volta_flag:
            cur_pass = 1
            in_volta_flag = False
        if i in volta:
            if cur_pass not in volta[i]:
                i += 1
                continue
            in_volta_flag = True
        order.append(i)
        if i in ends:
            if i not in jumped:
                jumped.add(i)
                cur_pass += 1
                i = start
                in_volta_flag = False
                continue
            if i not in volta:
                cur_pass = 1
        i += 1
    return order


# ---------------------------------------------------------------- 速度估算
def quarter_bpm(mm):
    try:
        v = mm.getQuarterBPM()
        if v:
            return float(v)
    except Exception:
        pass
    return float(mm.number) if getattr(mm, "number", None) else None


def measure_seconds(part, measures):
    """按谱面速度标记估算每个小节的时长(秒)，缺速度处按 120 估计。返回 (列表, 是否有缺失)。"""
    bounds = []
    try:
        for s0, e0, mm in part.flatten().metronomeMarkBoundaries():
            bounds.append((float(s0), float(e0), quarter_bpm(mm)))
    except Exception:
        pass
    missing = (not bounds) or any(b[2] is None for b in bounds)
    out = []
    for m in measures:
        a = float(m.offset)
        b = a + float(m.duration.quarterLength)
        sec = 0.0
        covered = 0.0
        for s0, e0, bpm in bounds:
            ov = min(b, e0) - max(a, s0)
            if ov > 0:
                covered += ov
                sec += ov * 60.0 / (bpm or 120.0)
        rest = (b - a) - covered
        if rest > 0:
            sec += rest * 60.0 / 120.0
        out.append(sec)
    return out, missing


# ---------------------------------------------------------------- 主流程
def process(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    log(f"\n===== {os.path.basename(path)} =====")
    s = converter.parse(path)
    parts = list(s.parts)
    all_measures = [list(p.getElementsByClass("Measure")) for p in parts]
    p0, ms0 = parts[0], all_measures[0]
    n = len(ms0)

    # 1) 声部表
    rows = []
    counts = set()
    for i, (p, ms) in enumerate(zip(parts, all_measures)):
        name = clean(p.partName)
        ins = p.getInstrument(returnDefault=False)
        tr = ins.transposition if ins is not None else None
        counts.add(len(ms))
        rows.append([i, name, str(tr) if tr is not None else "", len(ms)])
    tr_list = [translate(r[1]) for r in rows]
    dup = Counter(z for z, _, _ in tr_list)
    seen = defaultdict(int)
    zh_rows = []
    for (z, g, note) in tr_list:
        if dup[z] > 1:
            seen[z] += 1
            z = f"{z} {seen[z]}" if not re.search(r"\d", z) else f"{z}（第{seen[z]}条）"
            note = (note + " 重名声部，按出现顺序编号，请核对").strip()
        zh_rows.append((z, g, note))
    for r, (z, g, note) in zip(rows, zh_rows):
        log(f"  [{r[0]:02d}] {r[1]:28} 小节数={r[3]:4} 移调={r[2] or None}  →  {z} / {g} {note}")
    log("  小节数一致:", len(counts) == 1)

    # 2) 拍号、速度、排练记号
    ts_list = [(t.measureNumber, t.ratioString)
               for t in p0.recurse().getElementsByClass(meter.TimeSignature)]
    log("  拍号变化:", ts_list)
    marks = [(t.measureNumber, t.text or "", t.number)
             for t in p0.recurse().getElementsByClass(tempo.MetronomeMark)]
    log("  速度标记:", marks)
    outliers = [m for m in marks if m[2] and (m[2] > 200 or m[2] < 20)]
    log("  速度异常(>200 或 <20):", outliers)

    rehearsal = {}
    for ms in all_measures:
        for i, m in enumerate(ms):
            for r in m.recurse().getElementsByClass(expressions.RehearsalMark):
                rehearsal.setdefault(i, str(r.content))
    log("  排练记号数:", len(rehearsal))

    # 3) 时值检查（拍号以“该小节生效的拍号”为准）
    bad = []
    for p, ms in zip(parts, all_measures):
        ts_now = None
        for m in ms:
            if m.timeSignature is not None:
                ts_now = m.timeSignature
            if ts_now is None or m.paddingLeft or m.paddingRight:
                continue
            if abs(m.duration.quarterLength - ts_now.barDuration.quarterLength) > 0.01:
                bad.append((clean(p.partName), m.number))
    log("  时值异常小节数:", len(bad), bad[:20])

    # 4) 反复结构与演奏顺序
    starts, ends, volta = read_repeats(ms0, p0)
    order = play_order(n, starts, ends, volta)
    nums = [m.number for m in ms0]
    log("  反复起点(小节编号):", [nums[i] for i in sorted(starts)])
    log("  反复终点(小节编号):", [nums[i] for i in sorted(ends)])
    log("  房子(小节编号->第几遍):",
        {nums[i]: sorted(v) for i, v in sorted(volta.items())})
    log(f"  谱面小节数={n}  展开反复后的演奏小节数={len(order)}")

    # 5) 按谱面速度估算时长（仅供参考）
    secs, missing = measure_seconds(p0, ms0)
    plain_total = sum(secs)
    played_total = sum(secs[i] for i in order)
    note = "（有小节缺速度数据，按 120 估算，仅作粗略参考）" if missing else ""
    log(f"  按谱面速度估算时长: 不含反复 {plain_total/60:.1f} 分钟 / 含反复 {played_total/60:.1f} 分钟 {note}")
    start_sec, acc = [], 0.0
    for i in order:
        start_sec.append(round(acc, 3))
        acc += secs[i]

    # 6) 各声部活跃区间
    active = []
    for i, (p, ms) in enumerate(zip(parts, all_measures)):
        flags = [any(True for _ in m.recurse().notes) for m in ms]
        ranges = []
        for k, on in enumerate(flags):
            if not on:
                continue
            if ranges and k - ranges[-1][1] - 1 <= GAP:
                ranges[-1][1] = k
            else:
                ranges.append([k, k])
        active.append({"index": i, "name": rows[i][1], "zh_name": zh_rows[i][0],
                       "group": zh_rows[i][1], "transposition": rows[i][2],
                       "ranges": ranges})
        if (i + 1) % 10 == 0:
            say(f"    声部进度 {i+1}/{len(parts)}")

    # 7) 写出文件
    with open(f"out/{stem}_parts.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["index", "name", "transposition", "zh_name", "group", "note"])
        for r, (z, g, note) in zip(rows, zh_rows):
            w.writerow([r[0], r[1], r[2], z, g, note])
    data = {
        "file": os.path.basename(path),
        "n_measures": n,
        "measure_numbers": nums,
        "repeats": {"starts": sorted(starts), "ends": sorted(ends),
                    "volta": {str(i): sorted(v) for i, v in sorted(volta.items())}},
        "play_order": order,
        "nominal_start_sec": start_sec,
        "tempo_marks": [{"measure_number": a, "text": b, "bpm": c} for a, b, c in marks],
        "time_signatures": [{"measure_number": a, "ts": b} for a, b in ts_list],
        "rehearsal": [{"index": i, "measure_number": nums[i], "mark": rehearsal[i]}
                      for i in sorted(rehearsal)],
        "parts": active,
    }
    with open(f"out/{stem}_structure.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    return {
        "file": os.path.basename(path), "parts": len(parts), "measures": n,
        "played": len(order), "rehearsal": len(rehearsal), "bad": len(bad),
        "outliers": len(outliers), "repeats": len(ends),
    }


def main():
    files = sorted(set(glob.glob("*.mxl") + glob.glob("*.musicxml")))
    if not files:
        say("No .mxl files found next to analyze.py")
        log("没有找到 .mxl 文件：请把乐谱放在和本脚本相同的文件夹里。")
        return
    summary = []
    for idx, f in enumerate(files, 1):
        say(f"[{idx}/{len(files)}] parsing: {f}  (large scores may take a few minutes)")
        t0 = time.time()
        try:
            summary.append(process(f))
            say(f"    done in {time.time()-t0:.0f}s")
        except Exception:
            log(f"\n!!! 处理失败: {f}\n{traceback.format_exc()}")
            say(f"    FAILED: {f} (see report.txt)")
    log("\n===== 各乐章概览 =====")
    log("文件 | 声部数 | 谱面小节 | 演奏小节(展开反复) | 排练记号 | 时值异常 | 速度异常 | 反复终点数")
    for r in summary:
        log(f"{r['file']} | {r['parts']} | {r['measures']} | {r['played']} | "
            f"{r['rehearsal']} | {r['bad']} | {r['outliers']} | {r['repeats']}")
    REPORT.close()
    say("All done. Open report.txt and the out folder.")


if __name__ == "__main__":
    main()
