"""全片渲染（2026-11-09，用户：第 1 版全片 2560×1440、120 帧/秒；太长会出问题，分段同时渲染再拼接）。

结构：片头封面 → 第一至第四乐章（每个乐章开头 7 秒过渡，结尾留几秒让谱面滚出）→ 片尾参考资料。
每个乐章按对齐表切成约 45 秒的段，每段单独排谱（前后多排约 20 秒的小节作衔接，保证接缝处画面一致），
单独起一个无头 Edge 逐帧截图（设备缩放 4/3，1920×1080 的版面直接出 2560×1440），截图经管道直接交给 ffmpeg 编码，
不落地存帧。每段只出画面；声音最后按乐章从原录音整段截取，避免分段接缝。

时间轴：每个乐章内部以录音时刻为准，帧时刻 = 乐章起点 + 帧号 / 120，乐章起点 = 第一小节时刻 − 过渡时长。
段与段在帧号上首尾相接，拼起来逐帧连续。乐章之间的静音 = 上一乐章结尾留白 + 下一乐章开场过渡。

用法（在项目根目录）：
  python -X utf8 demo/render_film.py plan                 # 生成 film/plan.json
  python -X utf8 demo/render_film.py worker 1             # 起一个渲染进程（编号决定调试端口）；可同时起多个
  python -X utf8 demo/render_film.py status               # 进度
  python -X utf8 demo/render_film.py assemble             # 全部段完成后：拼画面、做声音、写章节，输出 film/马勒六_第1版.mp4
可续跑：已完成的段（film/chunks/<id>.mp4）跳过；中断的段重渲。
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import render_frames as rf  # noqa: E402  复用其中的 WebSocket 客户端与浏览器启动

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
FILM = ROOT / "film"
PY = sys.executable          # 排谱、渲染用的 Python（装了 verovio 的那个）
# 输出规格由 plan 写进 film/plan_settings.json（2026-11-10：样片 2 用 1920×1080、60 帧/秒；正式版 3840×2160、120 帧/秒）
_SET = FILM / "plan_settings.json"
SETTINGS = json.loads(_SET.read_text(encoding="utf-8")) if _SET.exists() else {"w": 2560, "h": 1440, "fps": 60, "name": "马勒六_第1版_2560x1440_60fps", "title": "第 1 版"}
FPS = SETTINGS["fps"]
W, H = SETTINGS["w"], SETTINGS["h"]
# 声音来源：'mkv' = 完整演出 6.mkv 的 FLAC 48 kHz / 24 位，成片为 .mkv（FLAC 24 位）；否则按乐章 m4a 截取，成片 .mp4（AAC）
MKV_AUDIO = SETTINGS.get("audio") == "mkv"
OFFSETS = json.loads((ROOT / "align" / "cache" / "track_offsets.json").read_text(encoding="utf-8"))
INTRO = 7.0                       # 与 player.html 的 INTRO 一致（渲染时核对）
TAIL = {1: 4.0, 2: 4.0, 3: 4.0, 4: 6.0}   # 乐章最后一小节之后留的秒数：谱面滚出、余响消散
CHUNK_SEC = 180.0                 # 每段目标长度（秒）。2026-11-10 起，反复跳转之间的一整段音乐只排一次谱，
                                  # 各段共用同一份排谱、只分帧号，接缝两侧逐像素一致（用户：一个进程只负责一个乐章的排版）
CONTEXT_SEC = 45.0                # 反复跳转后的那一整段，前面多排的音乐长度（秒）
AUDIO = {1: "audio/01 Symphony No. 6 in A Minor_ I.m4a"}


def rows_of(mv):
    return json.loads((ROOT / "align" / f"m{mv}_measures.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- plan
def plan(args=()):
    """plan [--res 1920x1080] [--fps 60] [--name 输出文件名] [--title 版本说明]"""
    a = list(args)

    def opt(k, d):
        return a[a.index(k) + 1] if k in a else d
    w, h = map(int, opt("--res", f"{W}x{H}").split("x"))
    fps = int(opt("--fps", FPS))
    single = opt("--single", "").split(",")
    st = {"w": w, "h": h, "fps": fps, "name": opt("--name", f"马勒六_{w}x{h}_{fps}fps"), "title": opt("--title", ""),
          "audio": opt("--audio", "m4a")}
    FILM.mkdir(exist_ok=True)
    _SET.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    segs = [{"id": "00_cover", "kind": "page", "page": "cover.html"}]
    for mv in (1, 2, 3, 4):
        al = rows_of(mv)
        rows = al["measures"]
        n = len(rows)
        end = rows[-1]["audio_sec"] + rows[-1]["audio_dur"]
        tau_s = rows[0]["audio_sec"] - INTRO
        nf = round((end + TAIL[mv] - tau_s) * fps)
        # 连续段（反复跳转处断开）：每段只排一次谱
        runs, a0 = [], 0
        for i in range(n - 1):
            if rows[i + 1]["position"] != rows[i]["position"] + 1:
                runs.append((a0, i + 1)); a0 = i + 1
        runs.append((a0, n))
        for r, (k0, k1) in enumerate(runs):
            f0 = 0 if r == 0 else round((rows[k0]["audio_sec"] - tau_s) * fps)
            f1 = nf if r + 1 == len(runs) else round((rows[k1]["audio_sec"] - tau_s) * fps)
            # 跳转后的那一段，前面多排约 CONTEXT_SEC 的小节（谱面上线性相邻的），至少 6 个
            pre = 0
            if r:
                p0 = k0
                while p0 > 0 and rows[k0]["audio_sec"] - rows[p0 - 1]["audio_sec"] < CONTEXT_SEC and rows[p0 - 1]["position"] == rows[k0]["position"] - (k0 - p0 + 1):
                    p0 -= 1
                pre = max(rows[k0]["position"] - rows[p0]["position"], min(6, rows[k0]["position"]))
            pos0 = rows[k0]["position"] - pre + 1                   # Verovio 位置从 1 起 = position + 1
            pos1 = rows[k1 - 1]["position"] + 1
            # --single 4：这个乐章只用一个进程从头渲到尾（用户 2026-11-10：终乐章多进程拼接可能不自然）
            nchunk = 1 if str(mv) in single else max(1, round((f1 - f0) / fps / CHUNK_SEC))
            cuts = [f0 + round((f1 - f0) * c / nchunk) for c in range(nchunk + 1)]
            for c in range(nchunk):
                segs.append({"id": f"{mv}_{r}_{c:02d}", "kind": "score", "mv": mv, "build": f"{mv}_r{r}",
                             "m0": rows[k0]["measure"], "m1": rows[k1 - 1]["measure"],
                             "range": f"{pos0}-{pos1}", "anchor": k0, "last": (n - 1) if r + 1 == len(runs) else None,
                             "intro": r == 0, "tau_s": tau_s, "f0": cuts[c], "f1": cuts[c + 1]})
    segs.append({"id": "99_credits", "kind": "page", "page": "credits.html"})
    (FILM / "plan.json").write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
    tot = sum(s["f1"] - s["f0"] for s in segs if s["kind"] == "score")
    nb = len({s["build"] for s in segs if s["kind"] == "score"})
    print(f"{st}；{len(segs)} 段，排谱 {nb} 次；乐章部分共 {tot} 帧（{tot / fps / 60:.1f} 分钟）")


# ---------------------------------------------------------------- worker
def build(seg):
    """同一份排谱由多个段共用：先拿到锁的进程排，别的进程等它排完。"""
    out = FILM / "build" / seg["build"]
    if (out / "data.js").exists():
        return out
    (FILM / "locks").mkdir(parents=True, exist_ok=True)
    lock = FILM / "locks" / f"build_{seg['build']}.lock"
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(fd); break
        except FileExistsError:
            time.sleep(5)
            if (out / "data.js").exists():
                return out
    try:
        if (out / "data.js").exists():
            return out
        return _build(seg, out)
    finally:
        lock.unlink(missing_ok=True)


def _build(seg, out):
    cmd =[PY, "-X", "utf8", "-W", "ignore", str(DEMO / "build_score.py"), "--movement", str(seg["mv"]),
           "--range", seg["range"], "--anchor-row", str(seg["anchor"]), "--out", str(out) + ".tmp"]
    if not seg["intro"]:
        cmd += ["--meter-at-start"]
    if seg["last"] is not None:
        cmd += ["--last-row", str(seg["last"])]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not (pathlib.Path(str(out) + ".tmp") / "data.js").exists():
        raise RuntimeError("排谱失败：" + (r.stdout + r.stderr)[-2000:])
    if out.exists():
        import shutil; shutil.rmtree(out)
    os.replace(str(out) + ".tmp", out)
    return out


def render(seg, port, log):
    rf.PORT = port
    proc, url = rf.start_browser()
    ff = None
    try:
        ws = rf.WS(url)
        ws.s.settimeout(180)                                  # 浏览器失去响应时不至于永远卡住
        ws.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=W / 1920, mobile=False)
        ws.call("Page.enable")
        if seg["kind"] == "page":
            page = (DEMO / seg["page"]).as_uri()
        else:
            data = os.path.relpath(FILM / "build" / seg["build"] / "data.js", DEMO).replace("\\", "/")
            page = (DEMO / os.environ.get("PLAYER", "player.html")).as_uri() + "?data=" + data + "&film=1" + ("" if seg["intro"] else "&cont=1")
        ws.call("Page.navigate", url=page)
        for _ in range(600):
            time.sleep(0.1)
            try:
                if rf.evaluate(ws, "window.READY === true"):
                    break
            except RuntimeError:
                pass
        else:
            raise RuntimeError("页面没有就绪")
        info = json.loads(rf.evaluate(ws, "JSON.stringify({end: (window.SCORE && SCORE.clipEnd) || null, "
                                          "off: (window.SCORE && SCORE.audio && SCORE.audio.offset) || 0, intro: window.INTRO_SEC || null})"))
        if seg["kind"] == "page":
            n = round(info["end"] * FPS)
            times = [f / FPS for f in range(n)]
        else:
            if abs((info["intro"] or 0) - INTRO) > 1e-6:
                raise RuntimeError(f"播放页过渡时长 {info['intro']} 与 render_film.py 的 INTRO={INTRO} 不一致")
            times = [seg["tau_s"] + f / FPS - info["off"] for f in range(seg["f0"], seg["f1"])]
            if not seg["intro"]:
                # 后续段的第一帧因取整可能落在 t=0 前几毫秒，会被当成乐章开场过渡（谱头不钉住、卡片隐藏）；夹到 0
                times = [max(t, 0.0) for t in times]
            n = len(times)
        tmp = FILM / "chunks" / f"{seg['id']}.part.mp4"
        ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS),
                               "-c:v", "mjpeg", "-i", "-", "-vf", f"scale={W}:{H}:flags=lanczos:in_range=pc:out_range=tv,format=yuv420p", "-color_range", "tv",
                               "-c:v", "h264_nvenc", "-profile:v", "high", "-preset", "p5", "-rc", "constqp", "-qp", "17", "-g", str(2 * FPS),
                               "-r", str(FPS), "-an", "-movflags", "+faststart", "-f", "mp4", str(tmp)],
                              stdin=subprocess.PIPE)
        t_start = time.time()
        for i, t in enumerate(times):
            rf.evaluate(ws, f"renderAt({t:.6f})")
            jpg = ws.call("Page.captureScreenshot", format="jpeg", quality=95, captureBeyondViewport=False)["data"]
            ff.stdin.write(__import__("base64").b64decode(jpg))
            if (i + 1) % 600 == 0:
                log(f"{seg['id']}: {i + 1}/{n} 帧，{(i + 1) / (time.time() - t_start):.1f} 帧/秒")
        ff.stdin.close()
        if ff.wait() != 0:
            raise RuntimeError("ffmpeg 编码失败")
        ff = None
        os.replace(tmp, FILM / "chunks" / f"{seg['id']}.mp4")
        log(f"{seg['id']}: 完成 {n} 帧，用时 {(time.time() - t_start) / 60:.1f} 分钟")
    finally:
        if ff:
            ff.kill()
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def worker(wid, only=None):
    """only：只渲这个乐章的段，且不理会 hold 标记（暂缓乐章定下来后，单独起一个进程渲它）。"""
    port = 9340 + wid
    (FILM / "chunks").mkdir(parents=True, exist_ok=True)
    (FILM / "locks").mkdir(exist_ok=True)
    (FILM / "logs").mkdir(exist_ok=True)
    logf = open(FILM / "logs" / f"worker{wid}.log", "a", encoding="utf-8")

    def log(msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(line, flush=True); logf.write(line + "\n"); logf.flush()

    segs = json.loads((FILM / "plan.json").read_text(encoding="utf-8"))
    segs = [s for s in segs if s["kind"] == "score"] + [s for s in segs if s["kind"] == "page"]   # 片头片尾最后
    for seg in segs:
        done = FILM / "chunks" / f"{seg['id']}.mp4"
        lock = FILM / "locks" / f"{seg['id']}.lock"
        if done.exists():
            continue
        if only is not None:
            if seg.get("mv") != only:
                continue
        elif seg.get("mv") and (FILM / f"hold_m{seg['mv']}").exists():   # 用户还没定的乐章先不渲（终乐章新旧对齐待听）
            continue
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode()); os.close(fd)
        except FileExistsError:
            continue
        try:
            for attempt in (1, 2, 3):
                try:
                    if seg["kind"] == "score":
                        log(f"{seg['id']}: 排谱 第 {seg['m0']}–{seg['m1']} 小节（范围 {seg['range']}）")
                        build(seg)
                    render(seg, port, log)
                    break
                except Exception as e:                           # noqa: BLE001
                    log(f"{seg['id']}: 第 {attempt} 次失败：{e!r}"[:1500])
                    if attempt == 3:
                        log(f"{seg['id']}: 放弃")
        finally:
            lock.unlink(missing_ok=True)
    log("没有剩下的段了")


def status():
    segs = json.loads((FILM / "plan.json").read_text(encoding="utf-8"))
    done = [s for s in segs if (FILM / "chunks" / f"{s['id']}.mp4").exists()]
    busy = [s["id"] for s in segs if (FILM / "locks" / f"{s['id']}.lock").exists()]
    fr = lambda L: sum(s["f1"] - s["f0"] for s in L if s["kind"] == "score")
    held = [mv for mv in (1, 2, 3, 4) if (FILM / f"hold_m{mv}").exists()]
    print(f"{SETTINGS}\n完成 {len(done)}/{len(segs)} 段，乐章帧 {fr(done)}/{fr(segs)}；正在渲：{', '.join(busy) or '无'}"
          + (f"；暂缓：第 {held} 乐章" if held else ""))


# ---------------------------------------------------------------- assemble
def nframes(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return int(r.stdout.strip().strip(","))


def assemble():
    segs = json.loads((FILM / "plan.json").read_text(encoding="utf-8"))
    missing = [s["id"] for s in segs if not (FILM / "chunks" / f"{s['id']}.mp4").exists()]
    if missing:
        raise SystemExit("还有段没渲完：" + ", ".join(missing))
    (FILM / "concat.txt").write_text("".join(f"file 'chunks/{s['id']}.mp4'\n" for s in segs), encoding="utf-8")
    # 声音：片头静音；每个乐章从原录音整段截取（过渡开始早于音轨开头时补静音），开头 1.5 秒淡入，结尾 2.5 秒淡出；片尾静音
    parts, chapters, t = [], [], 0
    names = {1: "第一乐章 Allegro energico, ma non troppo", 2: "第二乐章 Andante moderato",
             3: "第三乐章 Scherzo. Wuchtig", 4: "第四乐章 Finale. Allegro moderato"}
    adir = FILM / "audio"; adir.mkdir(exist_ok=True)
    groups = [("片头", [segs[0]], None)] + [(names[mv], [s for s in segs if s.get("mv") == mv], mv) for mv in (1, 2, 3, 4)] \
        + [("参考资料", [segs[-1]], None)]
    for title, L, mv in groups:
        nf = sum(nframes(FILM / "chunks" / f"{s['id']}.mp4") for s in L)
        ns = nf * 48000 // FPS
        dur = nf / FPS
        wav = adir / f"{L[0]['id']}.wav"
        if mv is None:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                   "-af", f"atrim=end_sample={ns}"] + (["-c:a", "pcm_s24le"] if MKV_AUDIO else []) + [str(wav)]
        else:
            al = rows_of(mv)
            tau_s = L[0]["tau_s"]
            pad = max(0.0, -tau_s)
            src, ss = ROOT / al["audio"], max(0.0, tau_s)
            if MKV_AUDIO:
                # 2026-11-10 正式版：直接从完整演出 6.mkv 的 FLAC（48 kHz / 24 位）截取，前三乐章不再经 44.1 kHz 音轨重采样。
                # 音轨时间 + 偏移 = 视频时间（align/cache/track_offsets.json，整秒、零漂移）；音轨开头之前仍补静音，与原做法一致
                src, ss = ROOT / "6.mkv", OFFSETS[str(mv)] + max(0.0, tau_s)
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{ss:.6f}", "-i", str(src), "-map", "0:a:0",
                   "-af", f"aresample=48000,aformat=channel_layouts=stereo,adelay={int(round(pad * 1000))}:all=1,"
                          f"afade=t=in:st={pad:.3f}:d=1.5,apad,atrim=end_sample={ns},afade=t=out:st={dur - 2.5:.3f}:d=2.5",
                   "-ar", "48000"] + (["-c:a", "pcm_s24le"] if MKV_AUDIO else []) + [str(wav)]
        subprocess.run(cmd, check=True)
        parts.append(wav)
        chapters.append((title, t, t + dur)); t += dur
    (FILM / "audio_list.txt").write_text("".join(f"file 'audio/{p.name}'\n" for p in parts), encoding="utf-8")
    meta = [";FFMETADATA1", "title=马勒：A小调第六交响曲「悲剧」· 乐谱同步导赏" + (f"（{SETTINGS['title']}）" if SETTINGS.get("title") else "")]
    for title, a, b in chapters:
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(a * 1000)}", f"END={round(b * 1000)}", f"title={title}"]
    (FILM / "chapters.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    out = FILM / f"{SETTINGS['name']}.{'mkv' if MKV_AUDIO else 'mp4'}"
    acodec = (["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"] if MKV_AUDIO
              else ["-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart"])
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(FILM / "concat.txt"),
                    "-f", "concat", "-safe", "0", "-i", str(FILM / "audio_list.txt"),
                    "-i", str(FILM / "chapters.txt"), "-map", "0:v", "-map", "1:a", "-map_metadata", "2", "-map_chapters", "2",
                    "-c:v", "copy"] + acodec + [str(out)], check=True)
    print("已输出", out, f"共 {t / 60:.1f} 分钟")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "plan":
        plan(sys.argv[2:])
    elif cmd == "worker":
        worker(int(sys.argv[2]), int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[3] == "--only" else None)
    elif cmd == "status":
        status()
    elif cmd == "assemble":
        assemble()
    elif cmd == "test":          # test <段 id> <起始帧偏移> <帧数>：只渲一小截到 film/test_<id>.mp4，量速度、看画面
        segs = {s["id"]: s for s in json.loads((FILM / "plan.json").read_text(encoding="utf-8"))}
        seg = dict(segs[sys.argv[2]]); a, nfr = int(sys.argv[3]), int(sys.argv[4])
        (FILM / "chunks").mkdir(parents=True, exist_ok=True)
        if seg["kind"] == "score":
            build(seg)
            seg["f0"] += a; seg["f1"] = seg["f0"] + nfr
        seg["id"] = "test_" + seg["id"]
        render(seg, 9339, print)
