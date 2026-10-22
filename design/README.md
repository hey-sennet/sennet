# Strata

The chosen voice UI direction: horizontal lines through a circle, lifted by the voice like a
ridgeline plot. Instrument Serif for spoken words (italic for the agent), Inter for chrome. The
client sets words in Newsreader instead, which stays readable at body sizes.
This is a design reference for the React client, not product code.

```sh
python3 -m http.server 8731 --bind 127.0.0.1 --directory design
# open http://127.0.0.1:8731/strata.html   ("mic" in the bar drives it with your voice)
```

How it moves:

- **Swell.** The voice lifts one broad mound across all lines, peaking at about a quarter of
  the radius, so motion stays coordinated.
- **Eased, not sluggish.** Loudness drives a critically damped spring (`SPRING`), so the
  mound follows the voice within about 70 ms but every change eases in and out, with no
  jumps or overshoot. Width follows the voice's brightness more slowly, and the
  mound stays centred.
- **Organic, not plotted.** The voice reaches the centre of the mound first and rolls out to
  the rim over `TRAVEL` (about 0.2 s), like a soft material, and every line carries a faint
  undulation, so the lines never look ruled.
- **Light moves through it.** Opacity comes from a drifting noise field across the disc,
  coherent across lines and along each one, so soft brighter and dimmer patches wander
  through the surface. The voice drives the drift: patches creep at rest (`SHADE_REST`) and
  surge with loudness (`SHADE_VOICE`). The field fades out towards the rim so the disc keeps
  its clean circular edge. `SHADE` sets how dim the dimmest patches get.
- **Colour per speaker.** Your voice is cream; the agent, thinking or speaking, is glacier blue (`--agent`);
  approvals are amber. At rest the mound breathes slowly.
- **Thinking.** Rings spread from the centre like a drop in still water, fading as they widen.

How it is laid out:

- **Phone.** One column: the disc, the words beneath it, and a dock with type-instead and
  tasks. Tasks open as a bottom sheet; tapping one shows its timeline in the same sheet.
- **Desktop.** From 1024 px a task rail sits on the left. From 1280 px the selected task's
  timeline lives in a side drawer, collapsed to a chevron tab at the right edge by default;
  opening it, or picking a task, widens a third column so the disc glides aside.
- **Timeline.** Your request in serif, then commands with their results, files edited and the
  agent's reply in italic. A task's approval sits at the end of its own timeline; approvals
  for other tasks show as amber dots, never in another task's timeline.

`?state=listening&t=3.3&shot` renders one frozen frame at 3.3 s. `&panel` opens the desktop drawer and `&task=1` picks a task
(and opens it in the phone's sheet). `node design/capture.mjs`
films listening → speaking → rest into `design/out/` (ignored by git; needs ffmpeg and a
Playwright Chromium, installed by `npx playwright install chromium` run in `frontend/`).

## Performance

`?perf` shows the frame rate and the time spent drawing each frame. `?points=` and `?stops=`
change the points and gradient stops per line, and `?flat`, `?nofill` and `?noop` drop the
gradient, the occluding fill, or all drawing, to show what each part costs.
`node design/bench.mjs` runs these under CPU throttling and saves a frozen frame of each to
`design/out/bench/`.

Listening state. Headless runs use a 390×844 viewport at 2×; the canvas is capped at 2× on phones too.

| Setting | Draw (script) | Frame rate |
| --- | --- | --- |
| Pixel 8, 60 points, 12 stops | 5.0 ms (p95 8.0) | 60 fps, p95 frame 16.7 ms |
| Pixel 8, the same before the script savings below | 7.0 ms (p95 9.0) | 54 fps, p95 frame 33.5 ms |
| Pixel 8, no gradient, before the savings | 5.0 ms (p95 7.0) | 58 fps, p95 frame 16.8 ms |
| i7-12650H headless at 1× / 4× / 6×, 120 points, 24 stops | 1.2 / 4.2 / 6.2 ms | |
| The same, 60 points, 12 stops (default) | 0.8 / 2.5 / 3.6 ms | |
| The same, 40 points, 8 stops | 0.5 / 2.0 / 2.6 ms | |

- 60 points and 12 stops look the same as 120 and 24 at phone size and halve the script time,
  so they are the defaults. Even 40 points draws smooth curves.
- Painting costs more than the script, mostly the gradient strokes and then the fills under
  each line. The number of stops barely matters; having a gradient at all does. On the Pixel
  8, trimming the script (a per-frame table for the delayed lift instead of a history search
  per point, `sqrt` over `Math.hypot`, no per-point arrays, thinking noise only while
  thinking) was enough to hold 60 fps with the gradients; the frame is pixel-identical.
- Headless Chromium paints canvases in software, so its frame rates under throttling are
  pessimistic and vary run to run; only its draw times are worth comparing. The Pixel 8 runs
  the script about 6× slower than the i7, so a phone two or three times slower than a Pixel 8
  would spend 10–15 ms per frame on script alone and has not been tested. If one drops frames,
  draw all lines in one colour and apply the fade and light field as a single mask pass.
