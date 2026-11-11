<div align="center">

# 跟谱听 · score-sync-guide

[简体中文](README.md) | **English**

Turn a MusicXML full score and a recording into a long-form, score-synchronized *guided listening* video — on native Windows.<br>
Ships with a Claude Code skill that documents the whole workflow (alignment, engraving, rendering, writing and checking analysis cards).

</div>

## What it is

The left side of the frame is a single line of full score scrolling right to left past a fixed playhead. Sounding notes turn red; only the parts that are currently playing are shown, fading in and out as they enter and leave, and the whole score zooms out like a camera pulling back during tutti passages. The right side shows analysis cards (form, themes, orchestration, expression markings, with footnotes) and the movement's form. Movements are joined by transition animations, and the film ends with a list of references.

The workflow comes from a real production: an 83-minute guide to Mahler's Symphony No. 6 (four movements). The code in this repository is the code used for that film; `examples/mahler6/` holds its data as a worked example.

## Relationship to score-sync-video (credits)

This project builds on and ports parts of [**tofucurd/score-sync-video**](https://github.com/tofucurd/score-sync-video) (MIT):

- **The per-onset alignment algorithm comes directly from it.** `align/vendor_ssv/` contains unmodified copies of its `scripts/score_sync/alignment.py` and `alignment_pipeline.py` (source commit in `SOURCE.txt`, license in the same folder). Our `align/ssv_align.py` calls its `dense_sequence`, `correct_guide` and `refine_attack_chain`, using our own bar-level alignment as the guide and manual pins as exact constraints.
- Several player ideas are borrowed from it: a fixed cursor, a stable set of parts, a two-level form structure, a table of display names for parts, and resumable chunked rendering.

score-sync-video does not support native Windows. This project is essentially its **Windows localization**, extended with things it does not have:

| | This project |
|---|---|
| Platform | Native Windows 11; no WSL, Node or Playwright. Headless Edge driven by a DevTools client written with the Python standard library only |
| Alignment | Bar-level DTW → per-onset refinement (score-sync-video's algorithm) → listening checks with the score on screen → manual pins only where it is still wrong, then refine again |
| Listening checks | "Dispute reels": suspicious spans rendered as short clips with score and audio, so even listeners who don't read music can check the sync |
| Content | Analysis cards, a form panel, motif highlight bands, footnotes and end credits |
| Checking | Cross-checks every card sentence against the score: does the named instrument play there, which part a marking is written in, dynamics, key and time signatures, number of parts |
| Rendering | One layout per movement shared by several processes split by frame ranges, so there are no visible seams; NVENC encoding; optional 24-bit / 48 kHz audio taken straight from a concert video's original track |

## Skip our detours

The approaches we tried and dropped are listed under "试过、不推荐" (tried, not recommended) in [`references/alignment.md`](skills/score-sync-guide/references/alignment.md). The most important lesson: **run per-onset refinement right after bar-level alignment, then listen, then add pins.** We did it the other way round — eight listening rounds and nearly a hundred manual pins and beats — and the per-onset refinement then fixed most of the in-bar offsets in one pass.

Windows-specific pitfalls (encodings, PowerShell, CRLF batch files, long-running detached processes, sample rates) are in [`references/windows-notes.md`](skills/score-sync-guide/references/windows-notes.md).

## Layout

```
skills/score-sync-guide/
  SKILL.md                the Claude Code skill: workflow, rules, commands
  references/             detailed notes: workflow / alignment / rendering / cards / windows-notes
  template/               the code; copy into your project root
    analyze.py            score health check
    align/                alignment (incl. vendor_ssv/ from score-sync-video)
    demo/                 engraving, player page, test clips, full-film rendering, dispute reels, opening and credits
    tools/                corrected copies, part tables, score-reading tools, card writing and checking
examples/mahler6/         worked example: analysis cards, form, motifs, part labels, references, alignment anchors and pins, correction log
```

## Requirements

- Windows 10/11 with Microsoft Edge (preinstalled)
- Python (3.15 was used): `verovio`, `music21`, `librosa`, `numpy`, `scipy`, `numba`; optional `partitura`, `parangonar`, `tqdm`. See `requirements.txt`.
- ffmpeg on PATH; a recent driver if you encode with an NVIDIA GPU.

## Usage

### As a Claude Code skill

Copy `skills/score-sync-guide/` into `~/.claude/skills/` (or your project's `.claude/skills/`), then describe the video you want in Claude Code, for example "make a score-following video from this mxl and this recording".

### Running the scripts yourself

1. Create a project folder, copy the contents of `template/` into it, and add your `.mxl` files and recordings (recordings go in `audio/`).
2. Write `manifest.json` following `examples/mahler6/manifest.json`.
3. Follow the "最短路径" (shortest path) section of `skills/score-sync-guide/SKILL.md`.

The code still contains constants specific to Mahler 6 (movement titles, repeat positions, opening text and so on); change them for another work. They sit at the top of each script or are marked by comments.

## Not included

For copyright reasons the repository contains **no** recordings or videos, no MusicXML scores or corrected copies of them, no score PDFs, no audio caches, no rendered films and no third-party logos. The logo slot in the end credits hides itself when the image is missing.

## Notes

- Built with the help of Claude Code. Code comments and notes are mostly in Chinese.
- Bar numbers follow the score file used. Community MusicXML files can contain errors and may not match the printed edition.
- The analysis texts in the example data were written for that film; sources are in `examples/mahler6/references.json`.

## License

MIT, see [LICENSE](LICENSE). Files under `align/vendor_ssv/` are © tofucurd and distributed under their MIT license. Other dependencies: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
