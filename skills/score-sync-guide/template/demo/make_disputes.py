"""争议片段合集：把四个乐章对齐结果里的低置信度段落（听检清单里的每一段），各前带 2 小节、后带 1 小节，
渲染成带谱面、带录音的短片，再拼成一支视频，每段一个章节。供用户对着谱面听，检查对齐有没有差错
（用户 2026-11-07：对着谱面听比听"嗒"声更靠谱）。

每段开头静止 1 秒，右栏顶部大字写明：乐章、小节、录音时刻、为什么可疑。可续跑：已渲染好的片段跳过。
反复：第一乐章反复第二遍的小节号与第一遍相同，用对齐表的行号区分；第二遍从第 122 小节直接跳到第 128 小节，
跨过跳转的片段在跳转处截断。

用法：python -X utf8 demo/make_disputes.py            # 渲染全部并拼接
      python -X utf8 demo/make_disputes.py --only 4:562  # 只渲染某一段（乐章:起始小节），用于试看
输出：demo/disputes/clip_*.mp4、demo/争议片段合集.mp4（带章节）
"""
import argparse
import json
import pathlib
import subprocess
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
CLIPS = DEMO / "disputes"
PY = sys.executable
PRE, POST = 2, 1          # 前带几小节、后带几小节
LEAD = 1.0                # 每段开头静止几秒
CLIP_TIMEOUT = 900        # 每段渲染限时（秒）；最长的一段正常约 3 分钟


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def regions(rows: list) -> list[tuple[int, int]]:
    """与 align/make_checklist.py 相同的合并规则：低置信度小节连成段，中间只隔一个正常小节的也并入。"""
    out, k = [], 0
    while k < len(rows):
        if not rows[k]["flags"]:
            k += 1
            continue
        j = k
        while j + 1 < len(rows) and (rows[j + 1]["flags"] or (j + 2 < len(rows) and rows[j + 2]["flags"])):
            j += 1
        out.append((k, j))
        k = j + 1
    return out


def plan() -> list[dict]:
    man = {m["id"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["movements"]}
    clips = []
    for mv in (1, 2, 3, 4):
        rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
        st = json.loads((ROOT / man[mv]["structure"]).read_text(encoding="utf-8"))
        n_pos = st["n_measures"]
        for a, b in regions(rows):
            k0, k1 = max(0, a - PRE), min(len(rows) - 1, b + POST)
            # 谱面位置必须连续（反复跳转处截断，保留包含 a 的那一边）。用对齐表的 position（0 起），
            # 不用小节号推算：第一乐章有两个小节都编号 128（位置 128、129），按小节号推算会从这里起错一位
            for k in range(a, k0, -1):
                if rows[k]["position"] != rows[k - 1]["position"] + 1:
                    k0 = k; break
            for k in range(a + 1, k1 + 1):
                if rows[k]["position"] != rows[k - 1]["position"] + 1:
                    k1 = k - 1; break
            pos0 = rows[k0]["position"] + 1                                  # Verovio 的 measureRange 从 1 起
            pos1 = min(n_pos, rows[k1]["position"] + 1 + 2)                 # 多排两小节给右侧谱面
            fl = sorted({f for q in range(a, b + 1) for f in rows[q]["flags"]})
            caption = (f"第 {mv} 乐章 · 第 {rows[a]['measure']}–{rows[b]['measure']} 小节\n"
                       f"录音 {mmss(rows[a]['audio_sec'])} · {'、'.join(fl)}\n"
                       f"（画面从第 {rows[k0]['measure']} 小节开始，前后各带一两小节）")
            clips.append({"mv": mv, "a": a, "b": b, "k0": k0, "k1": k1, "range": f"{pos0}-{pos1}",
                          "m_a": rows[a]["measure"], "m_b": rows[b]["measure"], "caption": caption,
                          "title": f"第{mv}乐章 第{rows[a]['measure']}–{rows[b]['measure']}小节 {'、'.join(fl)}",
                          "out": CLIPS / f"clip_{len(clips):02d}_m{mv}_b{rows[a]['measure']}_r{a}.mp4"})
    return clips


def plan_spots(path: pathlib.Path) -> list[dict]:
    """复查用：按 JSON 列出的 [乐章, 首行, 末行, 说明] 生成片段（行号是对齐表的行号，含首尾）。"""
    man = {m["id"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["movements"]}
    out = []
    for i, (mv, k0, k1, note) in enumerate(json.loads(path.read_text(encoding="utf-8"))):
        rows = json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))["measures"]
        n_pos = json.loads((ROOT / man[mv]["structure"]).read_text(encoding="utf-8"))["n_measures"]
        pos0 = rows[k0]["position"] + 1
        pos1 = min(n_pos, rows[k1]["position"] + 1 + 2)
        title = f"第{mv}乐章 第{rows[k0]['measure']}–{rows[k1]['measure']}小节 复查"
        caption = (f"复查 · 第 {mv} 乐章 · 第 {rows[k0]['measure']}–{rows[k1]['measure']} 小节\n"
                   f"录音 {mmss(rows[k0]['audio_sec'])} 起\n{note}")
        out.append({"mv": mv, "a": k0, "b": k1, "k0": k0, "k1": k1, "range": f"{pos0}-{pos1}", "m_a": rows[k0]["measure"],
                    "m_b": rows[k1]["measure"], "caption": caption, "title": title,
                    "out": CLIPS / f"spot_{i:02d}_m{mv}_b{rows[k0]['measure']}.mp4"})
    return out


def render(c: dict) -> None:
    build = DEMO / "build_dispute"
    subprocess.run([PY, "-X", "utf8", "-W", "ignore", str(DEMO / "build_score.py"), "--movement", str(c["mv"]),
                    "--range", c["range"], "--out", str(build), "--first-row", str(c["k0"]), "--last-row", str(c["k1"])],
                   check=True, stdout=subprocess.DEVNULL)
    q = "intro=0&cards=0&caption=" + urllib.parse.quote(c["caption"])
    cmd = [PY, "-X", "utf8", str(DEMO / "render_frames.py"), "--data", "build_dispute/data.js", "--query", q,
           "--t-start", str(-LEAD), "--out", str(c["out"]), "--frames", str(DEMO / "frames_dispute"), "--jpeg"]
    for attempt in (1, 2):                 # 2026-11-07：无头浏览器偶尔失去响应，整批卡住 1.5 小时；每段限时，超时就连浏览器一起结束再试一次
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if p.wait(timeout=CLIP_TIMEOUT) != 0:
                raise subprocess.CalledProcessError(p.returncode, cmd)
            return
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"    第 {attempt} 次超时（>{CLIP_TIMEOUT // 60} 分钟），已结束渲染进程", flush=True)
    raise RuntimeError(f"{c['title']}：两次都超时")


def concat(clips: list[dict]) -> pathlib.Path:
    """拼接：视频直接复制，音频统一重编码为 48 kHz（终乐章音轨 48 kHz、其余 44.1 kHz），每段一个章节。"""
    # 先把每段的音频统一转成 48 kHz 再拼接（2026-11-08 教训）：终乐章录音 48 kHz、其余 44.1 kHz，
    # 直接用 concat 拼时 ffmpeg 按第一段的格式读后面各段，终乐章的声音被拉慢、与画面错开，整支音频比视频长出几分钟
    norm = []
    for c in clips:
        n = c["out"].with_name(c["out"].stem + "_48k.mp4")
        if not n.exists() or n.stat().st_mtime < c["out"].stat().st_mtime:
            subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(c["out"]), "-c:v", "copy",
                            "-c:a", "aac", "-ar", "48000", "-b:a", "256k", str(n)], check=True)
        norm.append(n)
    lst = CLIPS / "list.txt"
    lst.write_text("".join(f"file '{n.name}'\n" for n in norm), encoding="utf-8")
    meta = [";FFMETADATA1"]
    t = 0
    for c in clips:
        d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(c["out"])], capture_output=True, text=True).stdout.strip())
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(t * 1000)}", f"END={int((t + d) * 1000)}", f"title={c['title']}"]
        t += d
    (CLIPS / "chapters.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    out = DEMO / ("复查片段.mp4" if clips and clips[0]["out"].name.startswith("spot_") else "争议片段合集.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-i", str(CLIPS / "chapters.txt"), "-map", "0:v", "-map", "0:a", "-map_metadata", "1",
                    "-map_chapters", "1", "-c", "copy",
                    "-movflags", "+faststart", str(out)], check=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="只渲染某一段：乐章:起始小节，如 4:562")
    ap.add_argument("--spots", default=None, help="复查清单 JSON（[乐章, 首行, 末行, 说明]），输出 demo/复查片段.mp4")
    args = ap.parse_args()
    CLIPS.mkdir(parents=True, exist_ok=True)
    clips = plan_spots(pathlib.Path(args.spots)) if args.spots else plan()
    if args.only:
        mv, m = (int(x) for x in args.only.split(":"))
        clips = [c for c in clips if c["mv"] == mv and c["m_a"] == m]
    print(f"共 {len(clips)} 段", flush=True)
    for i, c in enumerate(clips):
        if c["out"].exists() and c["out"].stat().st_size > 0:
            continue
        print(f"[{i + 1}/{len(clips)}] {c['title']}（谱面位置 {c['range']}）", flush=True)
        render(c)
    if not args.only:
        out = concat(clips)
        print("已输出", out)


if __name__ == "__main__":
    main()
