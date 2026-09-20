# AGENTS.md

Context for AI coding agents working on **CAN & CANopen Studio**, a CAN bus
analyser and transmit station with two front ends over one engine.

Detailed guidance lives in [`skills/`](skills/) as plain Markdown, readable by
any agent or human. Start with
[`skills/canopen-studio-dev/SKILL.md`](skills/canopen-studio-dev/SKILL.md).

## Shape of the project

| Path | Contents |
|---|---|
| `src/canopen_studio/` | Python package: Tkinter studio (`gui.py`), sniffer, MCP and A2A servers, OBD-II diagnostics (`diag/`), protocol decoders (`stack/`) |
| `crates/canopen-core/` | Rust engine: frames, SDO, NMT, PDO, EDS, ISO-TP, OBD-II, telemetry, UDP transport, virtual simulator, PyO3 bindings |
| `crates/canopen-cli/` | Rust CLI: sniff, simulate, echo, benchmarks |
| `crates/canopen-gui/` | Rust front end (Slint) |

The two front ends must agree. When you change how a frame is decoded on one
side, change the other: `crates/canopen-core/src/telemetry.rs` mirrors
`src/canopen_studio/stack/decoders/sevcon_gen4.py` and `cia402_generic.py`.

## Commands

Run everything through `just` (see `justfile` for the full list):

```bash
just setup          # uv sync
just check          # ruff format --check, ruff check, pytest
just gui            # Python studio
just rust-build     # cargo build --workspace
just rust-test      # cargo test --workspace
just rust-gui       # Rust/Slint front end
```

On Windows, `just` needs Git for Windows' `bin/bash.exe`; the justfile points
at it with `set windows-shell`. No recipe may use a `#!` shebang, because
`/usr/bin/env` does not exist there.

## Gates

CI (`.github/workflows/ci.yml`) enforces, and these must stay green:

```bash
uv run ruff format --check . && uv run ruff check .
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings   # warnings fail the build
cargo test --workspace
```

## Baselines

Know these before you interpret a test run:

- **`cargo test --workspace`: everything passes.** Any Rust failure is yours.
- **`uv run pytest`: 1024 passed, 1 skipped, nothing failing** — but only
  after `just rust-python`. `uv sync` does not build the compiled extension:
  the backend is plain setuptools and the Rust workspace sits beside it. Skip
  that step and about ninety tests fail on `ImportError: cannot import name
  'canopen_core'`, which looks like a broken tree and is not one.

## Conventions

- Comments explain *why*, not *what*. Match the density of the surrounding file.
- Tests are named as sentences describing the behaviour
  (`an_nmt_start_moves_the_simulated_node_to_operational`), not `test_foo`.
- Protocol work belongs in `canopen-core` where it can be unit-tested, not in
  the GUI event loop.
- Commit messages state the problem, then the change. End them with
  `Assisted-by: AI` — no co-author trailer, and never name a model.
- The `.gitignore` is a default-deny allowlist: a new top-level file is
  invisible to git until you add `!/<name>` to it.
