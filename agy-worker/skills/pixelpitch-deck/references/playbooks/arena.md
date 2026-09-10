# Playbook: arena (brilliance pass)

For: making load-bearing slides genuinely good instead of first-draft
average. Run when the deck exists and the ask is "make it brilliant",
"better", or when principles 4 (exhaust the design space) applies.

## Steps (verbatim)

1. Read the principles index. Identify the load-bearing slides: the opener,
   the closer, and the 1-2 body slides the argument most depends on.
2. For each chosen slide, spawn 2-3 parallel subagents. Each authors ONE
   genuinely different composition of the SAME slide: same assertion title
   and data, different plate specimen choice, different aid form (ledger
   timeline vs chart vs hero-stat), different rhythm. Each agent follows the
   source-roundtrip protocol on its own candidate file
   (`arena/slide-NN-a.html`, `-b.html`, `-c.html`) and renders it.
3. Inspect every candidate render yourself against the principles: argument,
   aid-carries-claim, on-template vocabulary, clean overlay. Then choose ONE
   keeper per slide and record WHY in one line each (the losing attempts
   stay on disk as evidence).
4. Promote keepers into `slides/`, re-run `deck stage`, reconvert, and
   run the full gate set (lint, slidify report, per-slide visual inspection
   vs plates).
5. Report: per slide the candidates considered, the keeper, and the reason.
   An arena with no recorded comparison didn't happen. Name each candidate by
   its id, `4a`, `4b`, `4c`, in the report and keep the losers on disk under
   those same names. The id is the whole point of writing them down: it costs
   the reader two characters to say "go with 4b" instead of describing the
   composition they meant, and the file is still there next turn to swap in.
   A comparison the reader cannot address is a comparison they can only accept
   or reject whole.

## Rules

- Candidates must differ in structure, not decoration — three card grids is
  one idea with three paint jobs.
- Never merge candidates into a frankenslide; pick one and, if needed, one
  named borrowing.
- The keeper's render, not your intention, is the decision's evidence.
