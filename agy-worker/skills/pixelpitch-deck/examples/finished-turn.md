# What a finished turn looks like

One worked turn on the authored-slides route, from staged workspace to the
last line. Read it once before your first deck turn so the shape of the
deliverable is not something you are inferring while you work.

## The workspace at the end

```
workspace/
  brief.md                          staged for you
  brand-guideline.md                staged for you
  style.json                        staged for you, when the user picked one
  slides/
    01-saturday-is-the-new-monday.html
    02-weekend-orders-outsell.html
    03-staffing-follows-demand.html
    04-move-the-roster.html
    index.json                      the running order, and nothing else
  slides.json                       yours, for the gates; not collected
  out/                              deck shot / deck render output; not collected
```

Only `slides/` is read back out. `slides.json`, `out/`, and anything else you
leave behind are your own working artifacts.

[slides-index.json](slides-index.json) is the `index.json` from that run. Four
entries for a four-slide brief, in presentation order, each naming a file that
exists. `index.json` decides the order, so the numeric filename prefixes are
for your convenience and are not read.

Write each slide file the moment it is good rather than at the end. The outer
process watches `slides/` and previews each file as it lands, so an early
slide is a slide the user sees early. Rewriting one is fine; the last write
wins.

## Running the gates

`deck check` reads a flat array of slides with inline HTML, which is a
different shape from `index.json`. Build it from the directory:

```bash
python3 - <<'PY' > slides.json
import json, pathlib
index = json.loads(pathlib.Path("slides/index.json").read_text())["slides"]
print(json.dumps([
    {"index": i, "title": s.get("title", ""), "role": s.get("role", "body"),
     "html": (pathlib.Path("slides") / s["file"]).read_text()}
    for i, s in enumerate(index)
]))
PY
deck check --workspace .
```

A clean run, from the two worked slides in `pixelpitch-slide-craft/examples/`:

```
{
  "defects": {},
  "native_area_ratio": null,
  "plates_pinned": false,
  "slides": 2
}

mechanical: pass
still owed: open every rendered PNG and compare it against its plate and its
base. This check reads HTML, geometry and plate hashes. It cannot see a
double-exposed title or an off-template aid.
```

`plates_pinned` is `false` because these two slides are not on the roundtrip
route and have no `layouts.json`. On a template roundtrip it must be `true`,
and a `clean/` directory without a contract beside it is itself a defect.

`native_area_ratio` is `null` because no `out/render.json` existed yet. That
is not a pass on editability; it means the question has not been asked.

The `still owed` line is the point of the command. `mechanical: pass` is a
statement about HTML and geometry, and the turn is not finishable on it
alone.

## Then look

```bash
deck shot --slides slides.json --out out/shots
```

Open every PNG. Not a sample, and not the ones you are unsure about. The
defects that survive `deck check` are precisely the ones only looking finds.

## The last line

```
DECK_COMPLETE
```

Exactly one line, and nothing after it. A `DECK_FAILED: three slides overflow
their content budget and I could not shorten the copy without losing the
argument` is a better outcome than a `DECK_COMPLETE` on a deck nobody opened.
