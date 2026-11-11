@echo off
rem 正式对齐流程（2026-11-07 用户定）：逐小节 DTW（align.py）→ 困难段落用 parangonar 局部精修（refine_parangonar.py）→ 听检清单。
rem 可双击运行，也可在终端运行。每个乐章的中间结果存在 align\cache\，中断后重跑会跳过已算好的步骤。
rem 结果：align\m1_measures.json … m4_measures.json（以及 .csv、_check.wav、_path.png；DTW 原结果另存 _measures_dtw.json），
rem 日志在 align\run_all.log，听检清单在 align\听检清单.txt
chcp 65001 >nul
cd /d "%~dp0.."
if not defined PY set PY=python
for %%M in (1 2 3 4) do (
  echo ===== 第 %%M 乐章 ===== >> align\run_all.log
  "%PY%" -X utf8 -W ignore align\align.py --movement %%M >> align\run_all.log 2>&1
  "%PY%" -X utf8 -W ignore align\refine_parangonar.py --movement %%M >> align\run_all.log 2>&1
)
"%PY%" -X utf8 align\make_checklist.py >> align\run_all.log 2>&1
echo 全部完成，详见 align\run_all.log 和 align\听检清单.txt
