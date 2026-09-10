# `atoms.yaml` — schema reference (CONTRACT-v2 §A, M1)

This file is the **single source of truth** for the slidify atom catalog.
Three downstream consumers read it:

1. **The matcher** (`slidify.patterns.matcher`) — reads `id`, `priority`,
   `match`, `emit`, `tag`. Existing behavior. Backward-compatible.
2. **The codegen** (`components/scripts/codegen-atoms.ts`, M2) — reads the
   new `renderer:` block to emit one TSX file per atom into
   `components/src/recipes/`.
3. **The contract test runner** (`components/src/__tests__/contract/`, M5)
   — reads the new `fixture:` block to round-trip render → walk → cluster
   → classify and assert atom-id stability.

**Rule of thumb:** any field added to a row MUST either (a) be ignored by
the matcher's existing pickup loop, or (b) be added to the loader's
allow-list in `_load_manifest_yaml`. M1 chose (a) — `renderer:` and
`fixture:` are pure additive metadata; the matcher loop never touches
them.

---

## File anatomy

```yaml
patterns:                        # top-level key (matcher convention)
  - id: atom-bg-mesh             # row 1
    priority: 52
    match: { ... }
    emit: { ... }
    renderer: { ... }            # NEW (M1) — codegen target
    fixture: { ... }             # NEW (M1) — contract test fixture
  - id: atom-bg-aurora-band      # row 2
    ...
```

Atoms live alongside the legacy tier-0 patterns; the matcher loads
`patterns.yaml` and `atoms.yaml` into a single sorted list.

---

## Per-row fields

### `id` (string, required)

Manifest-row id — `atom-<axis>-<variant>` (kebab-case). The
**user-facing atom id** lives in `match.anchor.data_atom_id` /
`emit.metadata.recipe` and uses the dotted form (`bg.aurora-band`).

```yaml
- id: atom-bg-aurora-band                  # row id
  match: { anchor.data_atom_id: bg.aurora-band }   # user-facing atom id
```

### `priority` (int, required)

Sort key. Atoms sit in the **50–82 band**:

| Range | Use |
| --- | --- |
| 50 | `comp.*` (structural, observe-only) |
| 52 | `bg.*` |
| 56 | `surf.*` |
| 58 | `type.*` |
| 60 | `type.*` namespace catch-alls |
| 62 | `mask.*` |
| 66 | `dec.*` |
| 70 | `data.*` |
| 74 | `motion.*` |
| 78 | `ui.*` |
| 80 | `comp.*` composite atoms (declared, not observe-only) |
| 82 | `anno.*` |

### `match` (object, required)

Matcher predicate clauses. Standard atom rows use:

```yaml
match:
  anchor.data_atom_id: bg.aurora-band      # exact id
```

or for namespace catch-alls:

```yaml
match:
  anchor.data_atom_namespace: type
```

Multiple ids can be matched with a list:

```yaml
match:
  anchor.data_atom_id:
    - type.stroke
    - type.stroke-thick
```

### `emit` (object, required)

What the matcher returns when the predicate fires:

```yaml
emit:
  kind: NativeShape | NativeText | NativeSvg | Composite | observe
  confidence: 0.95-0.99
  metadata:
    recipe: snake_case_id          # by convention atom_<axis>_<variant>
    axis: bg | surf | type | mask | dec | data | motion | ui | anno | comp
    # ...freeform per atom
```

### `tag` (string, optional, default `"decision"`)

`"structural"` → matcher records the hit but doesn't return a Decision.
Used for namespace catch-alls (`comp.*`, `dec.*` namespace rules).

### `renderer` (object, optional, NEW in M1)

**Codegen target.** Tells `codegen-atoms.ts` how to emit a TSX file.

```yaml
renderer:
  component: BgAuroraBand          # PascalCase TSX export name (required)
  tier: A | B                      # required: A=primitive, B=recipe
  primitive: frame.safe-area       # Tier-A primitive this delegates to (Tier-B only)
  composes:                        # for composite atoms (comp.*)
    - { atom: bg.aurora-band, props: { cy: 0.2 } }
    - { atom: surf.hero }
  version: 1.0.0                   # per-atom semver (frozen post-publish)
  props:                           # prop bag for the React component
    bbox:      { type: bbox, required: true }
    color:     { type: color, default: 'tokens.gradient.accent-grad' }
    intensity: { type: enum, values: [low, med, high], default: med }
```

#### `renderer.component`
PascalCase derived from the atom id: `bg.aurora-band` → `BgAuroraBand`,
`comp.hero-investor` → `CompHeroInvestor`.

#### `renderer.tier`
- **`A`** — structural primitive, lives in
  `components/src/primitives/<Component>.tsx`, hand-written. Counted in
  the ~21-strong Tier-A list (CONTRACT-v2 §B.1).
- **`B`** — generated recipe in `components/src/recipes/<Component>.tsx`,
  body delegates to a Tier-A primitive (or composes other atoms).

#### `renderer.primitive`
String reference to a Tier-A atom id (e.g., `frame.safe-area`,
`slot.heading`, `data.sparkline`). Codegen verifies the string maps to a
real Tier-A row (and vice versa: every Tier-A row has a primitive file).

#### `renderer.composes`
List of `{ atom: <id>, props: { ... } }`. Used for composite atoms
(`comp.*`). Each entry references another atom id; codegen wires
the parent's React component to render the composed children in
declaration order.

If a composite references an atom id that doesn't exist, codegen fails.
M1 enforces: never reference an atom outside the 10 frozen axes; if a
needed sub-atom doesn't exist, add it (in the appropriate axis) or
inline the composition with primitives.

#### `renderer.version`
Per-atom semver. Once a row is published AND referenced from a deck,
its version is frozen — bumping requires `version: 1.1.0` (additive
prop) or `2.0.0` (breaking prop change). M2 codegen embeds the version
in the generated TSX.

#### `renderer.props`
Prop bag for the generated React component. Each entry is
`<name>: { type, ... }`:

```yaml
props:
  bbox:      { type: bbox, required: true }
  color:     { type: color, default: 'tokens.gradient.accent-grad' }
  intensity: { type: enum, values: [low, med, high], default: med }
  cy:        { type: number, min: 0, max: 1, default: 0.2 }
  text:      { type: string, required: true }
  data:      { type: array, items: number, required: true }
  brand:     { type: object, required: true }
```

Supported `type` keywords (M2 codegen reads these):

| `type` | TSX target | Notes |
| --- | --- | --- |
| `bbox` | `Bbox` | from `ir/schema.ts` |
| `color` | `Color` | from `ir/schema.ts`; defaults may be `'tokens.<path>'` strings |
| `string` | `string` | |
| `number` | `number` | optional `min` / `max` |
| `boolean` | `boolean` | |
| `enum` | union of literals | `values: [low, med, high]` |
| `array` | `T[]` | `items: <type>` |
| `object` | `Record<string, unknown>` | freeform; refine in TSX manually if needed |

Defaults are passed through verbatim. Strings starting with `tokens.`
are token references that codegen resolves at render time via the
TokenBundle (`tokens.gradient.accent-grad`,
`tokens.palette.surface-2`, `tokens.elevation.floating`).

### `fixture` (object, optional, NEW in M1)

**Contract-test fixture.** Lets M5 round-trip render → walk → cluster →
classify and assert the matcher recovers the same atom id.

```yaml
fixture:
  sample_html: |
    <div data-atom="bg.aurora-band" style="position:absolute;...">
      <!-- the canonical structure for this atom -->
    </div>
  expected_recipe_id: bg.aurora-band       # MUST equal the user-facing atom id
```

`expected_recipe_id` MUST equal the user-facing atom id (i.e., the value
in `match.anchor.data_atom_id`). This is the contract: the renderer
emits, the matcher recovers the same id.

`sample_html` should be the minimal canonical markup for the atom — the
LLM-priming-table generator (a future M2 step) reads this verbatim to
prime the LLM authoring path.

---

## Naming conventions

### Atom ids
- **Axes:** lowercase, dotted. Frozen 10: `comp`, `bg`, `surf`, `type`,
  `mask`, `dec`, `data`, `motion`, `ui`, `anno`.
- **Variants:** lowercase, hyphenated for compound names
  (`bg.aurora-band`, `data.kpi-row`, `comp.hero-investor`).
- **Sub-atoms:** dotted-second segment (`brand.lockup-horizontal`,
  `text.eyebrow-ruled`). M1 keeps brand sub-atoms inside `surf.*` or
  `dec.*` to avoid creating an 11th axis (the 10 are frozen).

### Manifest row ids
- `atom-<axis>-<variant-with-hyphens-flattened>`. Examples:
  - `bg.aurora-band` → `atom-bg-aurora-band`
  - `comp.hero-investor` → `atom-comp-hero-investor`

### Component names
- PascalCase derived from atom id. Hyphens split words.
  - `bg.aurora-band` → `BgAuroraBand`
  - `comp.three-up-stats` → `CompThreeUpStats`

### Recipe metadata snake_case
- `emit.metadata.recipe` mirrors the row id with underscores:
  - `bg.aurora-band` → `atom_bg_aurora_band`
  - `comp.hero-investor` → `comp_hero_investor` (composite atoms drop
    the `atom_` prefix per CONTRACT-v2 §A.5)

---

## Validation rules (M1 + M2 enforce)

1. Every row's `id` is unique across the catalog.
2. `match.anchor.data_atom_id` MUST exist when `tag != "structural"`.
3. `emit.kind` MUST be one of `NativeShape | NativeText | NativeSvg |
   Composite | observe`.
4. `renderer.component` MUST be PascalCase, MUST be unique across
   catalog.
5. `renderer.tier` MUST be `A` or `B`.
6. If `renderer.tier == B` AND `renderer.composes` is absent, then
   `renderer.primitive` MUST be set and reference a real Tier-A atom.
7. If `renderer.composes` is set, every entry's `atom:` MUST reference a
   row in the catalog. (Codegen-time check; M1's job is to keep the
   catalog self-consistent.)
8. `fixture.expected_recipe_id` MUST equal the user-facing atom id from
   `match.anchor.data_atom_id`.
9. `renderer.version` MUST follow semver `<major>.<minor>.<patch>`.
10. The 10 axes are frozen. New axes require a CONTRACT amendment.

---

## Examples

### Tier-A primitive (structural)

```yaml
- id: atom-data-sparkline
  priority: 70
  match: { anchor.data_atom_id: data.sparkline }
  emit:
    kind: NativeSvg
    confidence: 0.99
    metadata: { recipe: atom_data_sparkline, axis: data, primitive: polyline }
  renderer:
    component: DataSparkline
    tier: A                    # primitive — lives in primitives/
    version: 1.0.0
    props:
      bbox:   { type: bbox, required: true }
      points: { type: array, items: number, required: true }
      stroke: { type: color, default: 'tokens.palette.accent-1' }
      width:  { type: number, default: 2 }
  fixture:
    sample_html: |
      <svg data-atom="data.sparkline" viewBox="0 0 200 60">
        <polyline points="0,40 40,30 80,35 120,20 160,25 200,10"
                  fill="none" stroke="currentColor" stroke-width="2"/>
      </svg>
    expected_recipe_id: data.sparkline
```

### Tier-B recipe (delegates to a primitive)

```yaml
- id: atom-bg-aurora-band
  priority: 52
  match: { anchor.data_atom_id: bg.aurora-band }
  emit:
    kind: NativeShape
    confidence: 0.95
    metadata: { recipe: atom_bg_aurora_band, axis: bg, fill: linear-5stop }
  renderer:
    component: BgAuroraBand
    tier: B
    primitive: frame.safe-area
    version: 1.0.0
    props:
      bbox:      { type: bbox, required: true }
      cy:        { type: number, min: 0, max: 1, default: 0.2 }
      colorA:    { type: color, default: 'tokens.gradient.accent-grad' }
      intensity: { type: enum, values: [low, med, high], default: med }
  fixture:
    sample_html: |
      <div data-atom="bg.aurora-band"
           style="position:absolute;inset:0;background:linear-gradient(120deg,
                  #ff7ad9 0%, #c084fc 25%, #6366f1 60%, #06b6d4 100%);
                  filter:blur(48px);opacity:0.85;"></div>
    expected_recipe_id: bg.aurora-band
```

### Composite atom (composes other atoms)

```yaml
- id: atom-comp-hero-investor
  priority: 80
  match: { anchor.data_atom_id: comp.hero-investor }
  emit:
    kind: Composite
    confidence: 0.99
    metadata: { recipe: comp_hero_investor, axis: comp }
  renderer:
    component: CompHeroInvestor
    tier: B
    composes:
      - { atom: bg.aurora-band, props: { cy: 0.2, intensity: med } }
      - { atom: bg.aurora-band, props: { cy: 0.85, intensity: low } }
      - { atom: surf.hero }
      - { atom: type.eyebrow-ruled }
      - { atom: type.headline-display }
    version: 1.0.0
    props:
      bbox:     { type: bbox, required: true }
      eyebrow:  { type: string, required: true }
      headline: { type: string, required: true }
      lede:     { type: string, required: false }
  fixture:
    sample_html: |
      <div data-atom="comp.hero-investor" data-pptx-role="slide"
           style="position:relative;width:1280px;height:720px;">
        <div data-atom="bg.aurora-band" style="..."></div>
        <div data-atom="surf.hero" style="..."></div>
        <span data-atom="type.eyebrow-ruled">PRESS RELEASE · MAY 2026</span>
        <h1 data-atom="type.headline-display">A new shape for the deck.</h1>
      </div>
    expected_recipe_id: comp.hero-investor
```

---

## Backward compatibility

The matcher (`slidify.patterns.matcher._load_manifest_yaml`) reads only
`id`, `priority`, `match`, `emit`, `tag`. Adding `renderer:` and
`fixture:` blocks is invisible to the runtime path. The 61 existing
rows continue to behave identically; M1's edit is pure additive
metadata.

If a future matcher upgrade ever consumes the new fields (e.g., to
read prop schemas for stricter validation), the loader's row pickup
needs to extend its allow-list. Until then, ignore-by-default is the
correct invariant.
