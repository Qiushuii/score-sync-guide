# Windows 本地化笔记

score-sync-video 不支持原生 Windows，默认输出 4K60 HEVC，并要求原谱 PDF。这里记下在原生 Windows 11 上跑通整条流程时遇到的问题和做法。

## 环境

- **不需要 WSL、Node、Playwright。** 浏览器用系统自带的 Edge 无头模式，`render_frames.py` 只用 Python 标准库实现 DevTools 协议的 WebSocket 客户端。
- **Python 3.15**：
  - `synctoolbox` 装不上（最新版只支持到 3.14，且限定 numba<0.66、music21<10、numpy<2.5，pip 会退回去编译老 numba 而失败）。对齐改用 librosa（依赖的 numba 0.68 有 3.15 安装包）。
  - `parangonar` 的包初始化需要 `tqdm`，但安装清单里漏了它，要单独装。
  - `cairosvg` 在 Windows 上缺 cairo 运行库，出图改用浏览器截图。
- ffmpeg 用 gyan.dev 的构建，带 NVENC/AMF/QSV。NVIDIA 显卡更新驱动后 NVENC 才可用。
- 无头 Edge 开 GPU（`EDGE_GPU=1`）实测没有提速；瓶颈在页面脚本和截图。

## 编码与终端

- 所有脚本都用 `python -X utf8` 运行，读写文件显式 `encoding="utf-8"`。
- **PowerShell 里运行带引号路径的程序，前面要加 `&`**：`& "C:\path with space\python.exe" -X utf8 script.py`，否则是解析错误。
- **批处理必须是 CRLF 换行。** 用 bash heredoc 写出的 .bat 是 LF，cmd 读不懂，夜间任务一步都没执行。
- PowerShell 5.1 里不要对原生程序用 `2>&1`（会把每行 stderr 包成错误记录）。

## 长时间渲染的进程

- 渲染要跑十几到几十个小时，**必须与启动它的终端/会话脱钩**。用 WMI 启动：

  ```powershell
  $cl = "cmd /c set PATH=D:\ffmpeg\bin;%PATH% && `"$py`" -X utf8 demo\render_film.py worker 1 > film\w1.out 2> film\w1.err"
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$cl; CurrentDirectory='C:\project'}
  ```

  这样启动的进程会各开一个空白 cmd 窗口，那就是渲染进程，不能关。
- **查进程用 `Get-CimInstance Win32_Process` 按命令行筛选。** git bash 的 `ps -W | grep python` 会漏掉进程，曾因此误判"渲染被结束"而重复启动，三个渲染抢同一个端口和帧目录。
- 每个 worker 用不同的调试端口和浏览器配置目录（编号决定）。
- 无头浏览器可能卡死而进程仍在：每段设超时，超时用 `taskkill /T /F` 连同浏览器一起结束再重试。卡死时用 DevTools 的 `Debugger.pause` 读调用栈能找到页面里的死循环（本项目是插值 0/0 产生 NaN，让"成组进出"的循环不前进）。
- 偶发 `MemoryError` 或 Edge 起不来，重试即可；大乐章整乐章排谱（SVG 近百 MB）时少开几个 worker。

## 声音

- 各乐章录音采样率不同（44.1 / 48 kHz）时，拼接前先统一采样率，否则后面的段被拉慢、与画面错开。
- 若有完整演出视频，可用互相关测出每条乐章音轨在视频音轨里的偏移（本项目是整秒、零漂移），成片直接从原始音轨截取，免去重采样。
