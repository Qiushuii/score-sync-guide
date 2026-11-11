"""逐帧渲染：用 Edge 无头模式打开 player.html，按时刻调用 renderAt(t) 并截图，再交给 ffmpeg 合成。

只用 Python 标准库，通过 Chrome DevTools 协议（WebSocket）控制浏览器，不需要装 Playwright/Node。
可续跑：已存在的帧会跳过；中断后重新运行即可从断点继续。

用法：
    python -X utf8 demo/render_frames.py            # 渲染全部帧并合成 mp4
    python -X utf8 demo/render_frames.py --probe 0 12 30   # 只截几个时刻的单帧，用于检查
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
PAGE = (ROOT / "player.html").as_uri()
FRAMES = ROOT / "frames"
OUT_MP4 = ROOT / "demo_m2_opening.mp4"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9333
FPS = 30
T_START = -4.5        # 乐章间过渡从 -4.5 秒开始（与 player.html 的 INTRO 一致）
T_END = 40.364        # 数据里没有 clipEnd 时的默认结束时刻（按谱面速度的第 9 小节开头）；有对齐数据时以 SCORE.clipEnd 为准


# ---------------- 极简 WebSocket 客户端（只够 DevTools 协议用） ----------------
class WS:
    def __init__(self, url: str):
        assert url.startswith("ws://")
        hostport, path = url[5:].split("/", 1)
        host, port = hostport.split(":")
        self.s = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        if b" 101 " not in buf.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手失败：" + buf.decode(errors="replace"))
        self.rest = buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _recv_exact(self, n: int) -> bytes:
        while len(self.rest) < n:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise ConnectionError("连接已关闭")
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126); head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127); head += struct.pack(">Q", n)
        mask = os.urandom(4)
        self.s.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        parts = []
        while True:
            b1, b2 = self._recv_exact(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n)
            if (b1 & 0x0F) in (0x9, 0xA):      # ping/pong，忽略
                continue
            parts.append(payload)
            if b1 & 0x80:
                return b"".join(parts).decode()

    def call(self, method: str, **params):
        self.next_id += 1
        mid = self.next_id
        self.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})


def evaluate(ws: WS, expr: str):
    r = ws.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
    if "exceptionDetails" in r:
        raise RuntimeError("页面脚本出错：" + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:1500])
    return r.get("result", {}).get("value")


def start_browser() -> subprocess.Popen:
    prof = ROOT / (".edge-profile" if PORT == 9333 else f".edge-profile-{PORT}")   # 换端口时用另一个配置目录，可与正在跑的渲染并存
    proc = subprocess.Popen([
        EDGE, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
        *(["--enable-gpu", "--ignore-gpu-blocklist", "--use-angle=d3d11", "--enable-gpu-rasterization", "--enable-zero-copy"]
          if os.environ.get("EDGE_GPU") == "1" else []),     # 2026-11-09 试验：让无头 Edge 用显卡画
        "--window-size=1920,1080", "--hide-scrollbars", "--force-device-scale-factor=1",
        "--allow-file-access-from-files", "--no-first-run", "--disable-extensions", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1) as r:
                pages = [p for p in json.load(r) if p.get("type") == "page"]
                if pages:
                    return proc, pages[0]["webSocketDebuggerUrl"]
        except OSError:
            pass
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("Edge 无头模式没有启动")


def open_page(ws: WS, data: str) -> None:
    ws.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
    ws.call("Page.enable")
    ws.call("Page.navigate", url=PAGE + "?data=" + data)   # data：相对 demo/ 的数据文件路径
    for _ in range(300):
        time.sleep(0.1)
        try:
            if evaluate(ws, "window.READY === true"):
                return
        except RuntimeError:
            pass
    raise RuntimeError("页面 60 秒内没有就绪：" + str(evaluate(ws, "document.title")))


def shot(ws: WS, t: float, path: pathlib.Path, jpeg: bool = False) -> None:
    evaluate(ws, f"renderAt({t:.5f})")
    opts = {"format": "jpeg", "quality": 92} if jpeg else {"format": "png"}
    png = ws.call("Page.captureScreenshot", captureBeyondViewport=False, **opts)["data"]
    path.write_bytes(base64.b64decode(png))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", nargs="*", type=float, help="只截这些时刻的单帧到 demo/probe/")
    ap.add_argument("--data", default="build/data.js", help="数据文件，相对 demo/，如 build_m4/data.js")
    ap.add_argument("--query", default="", help="附加到页面地址的参数，如 opdemo=1（只用于检查版式）")
    ap.add_argument("--out", default=None, help="输出 mp4 路径（默认 demo/demo_m2_opening.mp4）")
    ap.add_argument("--t-start", type=float, default=None, help="片段从第几秒开始（默认 -4.5，含乐章间过渡）")
    ap.add_argument("--frames", default=None, help="帧目录（默认 demo/frames）")
    ap.add_argument("--port", type=int, default=None, help="浏览器调试端口（默认 9333）；与另一个渲染同时跑时换一个，如 9334")
    ap.add_argument("--page", default=None, help="换一个页面（相对 demo/），如 credits.html（片尾）；默认 player.html")
    ap.add_argument("--t-end",type=float, default=None, help="片段到第几秒结束（默认 clipEnd）；乐章结尾要留出谱面滚出、余响消散的时间时用")
    ap.add_argument("--jpeg",action="store_true", help="帧用 JPEG（质量 92）截图，比 PNG 快，用于审听用的合集")
    args = ap.parse_args()
    global T_START, FRAMES, OUT_MP4, PORT, PAGE
    if args.page:
        PAGE = (ROOT / args.page).as_uri()
    if args.t_start is not None:
        T_START = args.t_start
    if args.frames:
        FRAMES = pathlib.Path(args.frames)
    if args.out:
        OUT_MP4 = pathlib.Path(args.out)
    if args.port:
        PORT = args.port
    ext = "jpg" if args.jpeg else "png"

    proc, url = start_browser()
    try:
        ws = WS(url)
        open_page(ws, args.data + (("&" + args.query) if args.query else ""))
        info = json.loads(evaluate(ws, "JSON.stringify({clipEnd: SCORE.clipEnd || null, audio: SCORE.audio || null, intro: window.INTRO_SEC || null})"))
        if args.t_start is None and info.get("intro"):
            T_START = -info["intro"]            # 过渡时长以播放页为准（2026-11-09 起 7 秒）
        if args.t_end is not None:
            info["clipEnd"] = args.t_end
        t_end = info["clipEnd"] or T_END
        audio = info["audio"]                   # {"file": 音轨, "offset": 片段 t=0 在音轨里的秒数}；没有就出无声视频
        if args.probe is not None:
            pdir = ROOT / "probe" / (pathlib.Path(args.page).stem if args.page else pathlib.Path(args.data).parent.name)
            pdir.mkdir(parents=True, exist_ok=True)
            for t in args.probe:
                shot(ws, t, pdir / f"t{t:+07.2f}.png")
                print("已截图", t)
            return

        FRAMES.mkdir(exist_ok=True)
        # 续跑只在页面和数据没变时才有意义：记一个指纹，变了就清掉旧帧重来
        h = hashlib.sha256()
        for f in ((ROOT / args.page, ROOT / "credits_data.js") if args.page else (ROOT / "player.html", ROOT / args.data)):
            h.update(f.read_bytes())
        h.update(f"{T_START},{t_end},{FPS},{args.query},{ext}".encode())
        stamp = FRAMES / "_stamp.txt"
        if not stamp.exists() or stamp.read_text() != h.hexdigest():
            old = list(FRAMES.glob("*.png")) + list(FRAMES.glob("*.jpg"))
            for p in old:
                p.unlink()
            if old:
                print(f"页面或数据有改动，已清除旧帧 {len(old)} 张")
            stamp.write_text(h.hexdigest())
        n = int(round((t_end - T_START) * FPS))
        t0 = time.time(); done = 0
        for f in range(n):
            p = FRAMES / f"{f:05d}.{ext}"
            if p.exists() and p.stat().st_size > 0:
                continue
            tmp = p.with_suffix(".tmp")
            shot(ws, T_START + f / FPS, tmp, args.jpeg)
            tmp.replace(p)                      # 写完再改名，中断时不留半张图
            done += 1
            if done % 100 == 0:
                print(f"{f + 1}/{n} 帧，{done / (time.time() - t0):.1f} 帧/秒", flush=True)
        print(f"帧渲染完成，共 {n} 帧")
    finally:
        proc.kill()

    # 合成：画面帧 + 录音（音频是主时钟：片段 t 对应音轨 offset + t 秒，视频时刻 = t - T_START）
    dur = n / FPS
    cmd = ["ffmpeg", "-hide_banner", "-y", "-framerate", str(FPS), "-i", str(FRAMES / f"%05d.{ext}")]
    if audio:
        lead = -T_START - audio["offset"]          # 音轨第 0 秒应放在视频的第几秒；负数表示要从音轨中间开始
        ss, delay = max(0.0, -lead), max(0.0, lead)
        cmd += ["-ss", f"{ss:.3f}", "-i", str(ROOT.parent / audio["file"])]
        cmd += ["-map", "0:v", "-map", "1:a", "-af",
                f"adelay={int(delay * 1000)}:all=1,afade=t=in:st={delay:.3f}:d=0.8,afade=t=out:st={dur - 1.5:.3f}:d=1.5",
                "-c:a", "aac", "-b:a", "256k"]
        print(f"加入录音：{audio['file']}，音轨第 {ss:.2f} 秒对应视频第 {delay:.2f} 秒")
    cmd += ["-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(OUT_MP4)]
    subprocess.run(cmd, check=True)
    print("已输出", OUT_MP4)


if __name__ == "__main__":
    main()
