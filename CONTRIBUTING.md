# Contributing

Thanks for looking. This is a small, opinionated project — the rules below aren't bureaucracy, they
are the handful of decisions that keep a tool with multi-GB optional dependencies installable by
someone who only wants the 200 KB core.

## Getting set up

```bash
uv sync                                      # core CLI + dev tools
uv sync --extra server --extra generation    # + the API and the Gemini/Lyria adapters
uv run pytest                                # fast, no GPU needed
uv run cinemagraph doctor                    # what's configured, reachable, or broken
```

`dev` is a [PEP 735 dependency group](https://peps.python.org/pep-0735/) — synced by default, never
part of a real install. `server` and `generation` are real optional features, opt-in via `--extra`.

Frontend work additionally needs [bun](https://bun.sh):

```bash
cd frontend && bun install && bun run dev    # :5173, proxies the API
```

Install the hooks before your first commit — they run ruff, mypy, ty, biome, typos, and zizmor, and
they regenerate the frontend's API client:

```bash
uv run prek install          # note: prek, not pre-commit -- same config, faster runner
uv run prek run --all-files  # what CI runs
```

Or run the type/lint checks alone: `./scripts/lint.sh`, and `./scripts/format.sh` to fix.

## The three rules that actually matter

Everything else is ordinary taste. These three, if broken, are expensive to unwind — so a PR that
crosses one will be asked to change, even if the code is good.

**1. The core never gains a heavy dependency.** `src/cinemagraph/` depends on opencv, numpy, click,
and imageio. Not torch, not transformers, not fastapi. Where new code lives is decided by two
questions — does the core need this to animate an image, and how heavy is it — which produce four
tiers (`docs/DESIGN.md` §3.2): a core dependency, an optional extra in the same environment, a light
sibling module, or a fully isolated service with its own pyproject, lockfile, and container. Torch-
class dependencies are always the last one.

**2. The isolated services never share code with each other.** `machine-learning/`,
`sound-effects/`, and `image-generation/` each duplicate their model-lifecycle logic on purpose
(`docs/DESIGN.md` §3.3). It looks like copy-paste because it is. Sharing it would reintroduce
exactly the coupling that isolating them prevents, and the shared surface is a few dozen lines. The
one exception is `src/server/_external_service.py`, which is safe precisely because it's *client*
code living in the single codebase that calls all of them.

**3. Optional features degrade, never fail.** Every optional service is checked at request time,
never at startup. Missing or unreachable means a clean 503 from its route and `false` from
`GET /capabilities` — never a 500, and never a failed boot. If you add an optional capability, it
must be possible to run the whole application with it absent and notice nothing but its absence.

## Where things go

- `src/cinemagraph/` — the rendering engine. Pure-ish; knows nothing about HTTP or where inputs came
  from.
- `src/server/` — FastAPI. `app.py` holds routes only (parse, delegate, shape the response);
  every multi-step workflow lives in `service.py`, which is what makes those workflows testable
  without an HTTP round-trip.
- `src/asset_library/`, `src/assembly/`, `src/generation/` — cross-cutting siblings, deliberately
  not submodules of the core.
- `machine-learning/`, `sound-effects/`, `image-generation/` — isolated services. Each has its own
  README describing its contract.
- `frontend/src/client/` — **generated, never hand-edited.** A pre-commit hook regenerates it from
  the live OpenAPI schema whenever `src/server/` changes.

## Tests

`docs/DESIGN.md` §3.5: tests protect what must never silently break — the mask contract, the
loop-closure invariant, both pipelines producing decodable video, and the API surface degrading
correctly. Experimental code gets tests when it stabilizes, not before; chasing coverage on code
that may be deleted next week is waste.

Tests marked `integration` need a live stack and usually a real GPU, so they're excluded by default
(`addopts = "-m 'not integration'"`). Run them with `-m integration` against a running deployment.

`scripts/golden_check.py` is deliberately *not* a pytest test — it compares rendered pixels against
previous output, which is a regression check, not a correctness contract. See its docstring.

## Write down what didn't work

`docs/experiments/` is a lab notebook: one short file per experiment, using `TEMPLATE.md`. Please
add one **especially when something fails** — several files there exist only to stop someone
retrying an approach that already lost a day. If your change was driven by a measurement, the
measurement belongs in an experiment note, and the "why" belongs in the decision log at the bottom
of `docs/DESIGN.md`.

Read `docs/DESIGN.md` before making a structural decision. There's a good chance it's already
answered there, along with the reasoning and, often, the thing that was tried first and abandoned.

## Pull requests

- Keep the diff to one concern.
- Explain *why* in the commit message. This repo's history is a design record; "fix bug" wastes it.
- Run `uv run pytest` and `./scripts/lint.sh` before pushing.
- Don't commit `.env`, model weights, or anything under `data/`.

By contributing, you agree your contributions are licensed under this project's
[Apache-2.0](LICENSE) license (§5 of that license makes this explicit — no separate CLA).
