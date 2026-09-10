# Worked roundtrip artifacts

Two routes, both shown end to end with real values. Read the set for the route
the workspace puts you on.

## Mode A: clean plate plus native overlay

| File | What it is |
|---|---|
| [spec-slide-04.json](spec-slide-04.json) | An excerpt of what `deck spec` writes: shape tree in 1280x720 px space, placeholder roles, run styles. |
| [mode-a-overlay.html](mode-a-overlay.html) | The slide built from it. Every coordinate, family, size, weight and colour is traceable to a field in the spec. |

The shell is [../assets/overlay-base.css](../assets/overlay-base.css). Point
sizes convert at `px = pt * 4 / 3`, so the spec's 54pt title is 72px.

Three things the pair is there to show. The plate is `clean/slide-04.png` and
never `base/slide-04.png`. The overlay reuses the placeholder's own rect and
type rather than a new composition. The footer, painted out during the clean
step, is re-added as overlay text, because furniture that is not re-added
simply disappears from the deck.

## The seam route

| File | What it is |
|---|---|
| [authoring-plan.json](authoring-plan.json) | The admitted slide types, each with its seams and their tag and style allowlists. |
| [baselines/statement.html](baselines/statement.html) | The immutable baseline, with two paired `pp:seam` markers and protected furniture outside them. |
| [patches.json](patches.json) | The only file you write on this route. |

The allowlists in the plan are the whole contract. `title` admits `em` and
`strong` and no styles at all; `aid` admits a small SVG vocabulary plus five
style properties. A fragment outside its allowlist is refused, and the refusal
costs the turn.

`baseline_sha256` in the patch is the digest of the baseline on disk, copied
verbatim from `deck patch --list`. Recompute it yourself to see the pinning
work:

```bash
python3 - <<'PY'
import hashlib, pathlib
print(hashlib.sha256(
    pathlib.Path("baselines/statement.html").read_bytes()
).hexdigest())
PY
```

That prints `a25b2dca…`, the value in `patches.json`. Change one byte of the
baseline and every patch carrying the old digest is refused as stale, which is
how a plan that has drifted from its bundle gets caught before it ships.

Applying the patch replaces the two seam regions and leaves everything else
byte-identical, footer included. Validate yours with `deck patch --check`
before finishing; a rejection you find yourself costs seconds.
