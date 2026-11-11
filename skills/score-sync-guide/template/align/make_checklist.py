"""根据四个乐章的对齐结果生成听检清单 align/听检清单.txt。
用法：python -X utf8 align/make_checklist.py（每次重跑对齐后运行一次）
"""
import datetime
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def main() -> None:
    lines = [f"听检清单（自动生成，{datetime.date.today()}）",
             "用法：用播放器打开 align/m<N>_check.wav，跳到下面列出的时刻听：每小节开头应有一声轻\"嗒\"，排练记号处一声高音\"叮\"。",
             "听到\"嗒\"明显早于或晚于小节开头（拍子对不上），记下时刻和大概偏多少，告诉我即可，不需要看谱。",
             "★ 表示那几个小节是按谱面比例推算的（插值），最需要听。",
             "◆ 表示那几个小节经 parangonar 局部精修改过（四道检查都通过才采用），也请留意。", ""]
    for mv in (1, 2, 3, 4):
        p = OUT / f"m{mv}_measures.json"
        if not p.exists():
            continue
        R = json.loads(p.read_text(encoding="utf-8"))["measures"]
        regions, k = [], 0
        while k < len(R):
            if not R[k]["flags"]:
                k += 1
                continue
            j = k
            while j + 1 < len(R) and (R[j + 1]["flags"] or (j + 2 < len(R) and R[j + 2]["flags"])):
                j += 1
            fl = sorted({f for q in range(k, j + 1) for f in R[q]["flags"]})
            regions.append((R[k]["audio_sec"], R[j]["audio_sec"] + R[j]["audio_dur"], R[k]["measure"], R[j]["measure"], fl))
            k = j + 1
        lines.append(f"== 第 {mv} 乐章（m{mv}_check.wav）：开头 {mmss(R[0]['audio_sec'])} 第 {R[0]['measure']} 小节；另有 {len(regions)} 处低置信度 ==")
        lines.append(f"  开头检查：{mmss(R[0]['audio_sec'])}–{mmss(R[3]['audio_sec'])}  第 {R[0]['measure']}–{R[3]['measure']} 小节（起点已按 align/anchors.json 固定）")
        for a, b, m0, m1, fl in regions:
            star = "★" if "已插值" in fl else ("◆" if "已精修" in fl else " ")
            lines.append(f"{star} {mmss(a)}–{mmss(b)}  第 {m0}–{m1} 小节  {'、'.join(fl)}")
        lines.append("")
    (OUT / "听检清单.txt").write_text("\n".join(lines), encoding="utf-8-sig")
    print("\n".join(lines[:12]))
    print(f"……已写 {OUT / '听检清单.txt'}")


if __name__ == "__main__":
    main()
