# Python API

```python
import asyncio
from slidify import convert, convert_sync, ConversionConfig

# Sync — easiest for scripts.
result = convert_sync("deck.html", "deck.pptx", ConversionConfig.fast())
print(result.native_area_ratio)

# Async — when you already have an event loop.
async def main():
    cfg = ConversionConfig(run_oracle=True, run_tier3=True)
    return await convert("deck.html", "deck.pptx", cfg)

asyncio.run(main())
```

## Source forms

`convert(source, pptx_path, config)` accepts five source types:

```python
await convert(html_string,             "out.pptx", cfg)
await convert(Path("deck.html"),       "out.pptx", cfg)
await convert(Path("slides_dir/"),     "out.pptx", cfg)
await convert(["<html>...</html>", …], "out.pptx", cfg)

async def stream():
    async for s in fetch_from_db():
        yield s
await convert(stream(),                "out.pptx", cfg)
```

The streaming variant is true streaming — peak memory bounded by
`render_concurrency`, not deck size.

## Configuration recipes

```python
ConversionConfig.fast()       # no oracle, no LLM, no editability check
ConversionConfig.from_env()   # reads SLIDIFY_* env vars (containers, CI)
ConversionConfig(             # everything; defaults are sensible
    run_oracle=True,
    run_tier3=True,
    render_concurrency=4,
    differential_render=True,
    embed_fonts=True,
)
```

## Result fields

```python
class ConversionResult(BaseModel):
    pptx_path: str
    n_slides: int
    native_area_ratio: float           # 0..1, higher = more editable
    pattern_coverage: float            # 0..1, Tier-0 hit rate
    cache_hit_rate: float
    llm_calls: int
    total_cost_usd: float
    elapsed_seconds: float
    decisions_by_tier: dict[str, int]
    pattern_hits: dict[str, int]
    fidelity_reports: list[FidelityReport]
    editability_passed: bool
    editability_failing_slides: list[int]
    unmatched_signatures: list[UnmatchedSignature]
```

## Exceptions

```python
from slidify import (
    SlidifyError,            # base
    RenderError,             # browser / Playwright failures
    ClassificationError,     # tier1/2/3 pipeline failures
    EmitError,               # python-pptx output failures
    OracleError,             # SSIM / OCR oracle failures
)
```

All carry optional `slide_index` and `unit_id` attributes for attribution.
