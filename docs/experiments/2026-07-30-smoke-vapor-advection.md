# True advection for smoke/vapor (§5.4)

**Date:** 2026-07-30
**Question:** `docs/DESIGN.md` §5.4 flagged smoke/vapor as a known realism gap -- they only ever
modulate brightness in place, unlike `ripple.py`'s genuine pixel displacement for water. Can this
be fixed with the same technique, without regressing the loop-closure invariant or the existing
brightness texture?

## What was built

`clouds.py`'s shared `smoke`/`vapor` engine gained a second, independent displacement field
(`flow_amplitude`, `flow_freq_x/y`, `flow_phase_x/y`, `flow_cycles_x/y` -- its own random draw
via the existing `rng()`/`cycles_for()` helpers, not derived from the brightness field's own
layers) applied via `cv2.remap` -- the same technique `ripple.py` already uses for water -- before
the existing brightness `cloud_layer` is composited on top of the *warped* result. Both together
read as moving, textured smoke; either alone doesn't (pure warp looks like a rippling mirror,
pure brightness modulation looks like a shimmering overlay with no real motion).

Per-preset tuning: `smoke` gets `flow_amplitude=4.0` (more visible curl, matching its already
denser/more turbulent character), `vapor` gets `flow_amplitude=2.5` (subtler, since vapor's
existing `rise_hz` brightness carrier already covers most of its upward-motion cue -- the new
field only needs to add texture, not fight it).

## Verification

1. **Existing invariants hold**: `uv run pytest` -- 74 passed, including the loop-closure test
   suite (every effect must produce an identical frame at t=0 and t=1). The new displacement
   field's temporal term goes through the same `cycles_for()` helper as everything else in this
   file, so it's periodic by construction the same way.
2. **`golden_check.py` unaffected**: its blessed cases are `dust`+`ripple` only, not `smoke`/
   `vapor` -- confirmed no unrelated regression (`All 4 golden frames match`).
3. **Visual confirmation the effect actually does what it claims** -- not just "renders without
   crashing": rendered a real clip through `smoke`, cropped a fixed region around a sharp edge in
   the source photo (the test photo's rectangle corner) across 7 frames spread through the loop,
   and stacked the crops vertically. The edge's horizontal position visibly shifts left/right
   frame to frame -- genuine pixel displacement, not a brightness change at a fixed position.

## Verdict

- [x] Smoke/vapor now genuinely advect (pixels move), not just brightness-modulate in place --
  confirmed visually, not assumed from code review alone.
- [x] No regression to existing tests, golden frames, or the loop-closure invariant.
- [ ] Remaining §5.4 items (perspective-aware ripple, flow direction from mask shape) not started
  this pass -- this was scoped to the smoke/vapor advection gap specifically.
