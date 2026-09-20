# Launching and driving the studio

Read this before the first attempt. Several failure modes here look like
application crashes and are not.

## The commands

```bash
just gui          # Python studio  (= uv run canopen-studio)
just rust-gui     # Rust front end (= cargo run --release -p canopen-gui)
just rust-simulate port="1750"   # headless virtual bus from the CLI
```

For a debug build of the Rust front end, `cargo run -p canopen-gui` starts in
seconds instead of minutes.

## Trap: a GUI that exits 0 with no output

Both front ends exit with status 0 and print nothing when they cannot reach
the interactive desktop. That is what happens under a sandboxed tool runner:
Tk's `mainloop()` returns immediately and Slint's `run()` does the same.

It is not a crash and there is nothing to debug in the application. Launch the
GUI with the sandbox disabled. If a GUI ever exits 0 in under a second, check
this before anything else.

A related symptom: a child process started inside a shell job can be killed
when that job ends. If a window disappears between two commands, launch it
detached, or keep the launch and whatever you do next in a single invocation.

## Trap: the locked executable

`cargo build` fails with

```
error: failed to remove file `target\debug\canopen-gui.exe`
Caused by: Accès refusé. (os error 5)
```

when a previous run is still open. Stop the process first:

```powershell
Get-Process canopen-gui -ErrorAction SilentlyContinue | Stop-Process -Force
```

## Trap: the justfile shell on Windows

`just` needs a POSIX shell even for plain recipes, and Windows has none on
PATH — only the WSL stub at `C:\Windows\System32\bash.exe`. The justfile sets
`windows-shell` to Git for Windows' `bin/bash.exe`.

It must be `bin/bash.exe`, not `usr/bin/sh.exe`: only the former sets up the
MSYS PATH. Under `sh.exe` the coreutils are absent and `find` resolves to
Windows' `find.exe`. For the same reason no recipe may carry a `#!` shebang —
`/usr/bin/env` does not exist on Windows — so multi-line recipe bodies live in
`scripts/` instead.

## The virtual bus

The Rust front end runs its own simulator over a local UDP pair:

| End | Port |
|---|---|
| UI | 1750 |
| Simulated nodes | 1751 |

**The two ports must differ.** Sharing one socket makes each end receive the
frames it just sent. `spawn_udp_simulator_bound` binds a real port and answers
requests; `spawn_udp_simulator` binds an ephemeral one and can only transmit,
which is what the CLI's `simulate` wants.

The simulator answers NMT commands, SDO uploads of 0x1000/0x1008/0x606C, CAN
pings on 0x7E0, and OBD-II Mode 01 for the PIDs in `SUPPORTED_MODE01_PIDS`.
It starts both nodes **pre-operational**, so "Active Nodes: 0" on the
dashboard is correct until you send an NMT Start.

The Python studio does not auto-connect: tick **Simulate**, then **Connect**,
or its telemetry stays at zero.

## Verifying a change

Prefer protocol-level integration tests over driving the UI.
`crates/canopen-core/tests/simulator_udp.rs` opens a real socket pair and
asserts the round trips — NMT Start reaching Operational, an SDO upload
returning the device type, a ping echoing its sequence. That is repeatable;
clicking is not.

Synthetic mouse input against these windows is unreliable: Windows refuses
`SetForegroundWindow` to a process that is not already in front, and the
window can move between calls, so clicks computed from `GetWindowRect` land
somewhere else. What works, when a screenshot is genuinely needed:

- Capture with `PrintWindow` (flag `2`), not `CopyFromScreen`, so another
  application overlapping the window does not appear in the image.
- Click an empty part of the window first to bring it forward, then click the
  target, and keep the whole sequence inside one invocation.
- Look at the screenshot. A blank frame means the window never rendered.

If two attempts do not land, stop and verify the behaviour another way rather
than retrying.
