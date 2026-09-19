# Security Policy

## Reporting a vulnerability

**Please report privately, not in a public issue.**

Use GitHub's private vulnerability reporting:

**[→ Report a vulnerability](https://github.com/s-celles/canopen-studio/security/advisories/new)**

That opens a draft GitHub Security Advisory (GHSA) visible only to you and the
maintainer. If the link does not work — private reporting has to be enabled on the
repository — contact [@s-celles](https://github.com/s-celles) on GitHub and ask for a
private channel before sending any details.

Please do **not** open a public issue, a pull request, or a discussion for a suspected
vulnerability until it has been fixed and an advisory published.

### What to include

The more of this you can give, the faster it can be confirmed:

- What the impact is — what someone gains, and over what.
- The version (`canopen-studio --version`, or the commit), the operating system, and the
  Python version.
- Which component: the MCP server, the A2A server, the diagnostics write gate, the
  decoding-formula interpreter, profile or DBC/CSV loading, the CAN bridge, the updater.
- Steps to reproduce, ideally against the virtual bus or the ELM327 emulator rather than
  real hardware.
- Whether it needs local access, physical access to a vehicle, or neither.

### What to expect

This is a small project maintained by one person, so these are honest intentions rather
than contractual guarantees:

| | |
|---|---|
| Acknowledgement | within a few days |
| Initial assessment | within two weeks |
| Fix or a stated plan | depends on severity and on the complexity of the fix |
| Credit | offered in the advisory and the changelog, unless you prefer otherwise |

Coordinated disclosure is welcome. If you have a deadline in mind, say so in the report.

## Supported versions

Only the latest release receives security fixes. The project is pre-1.0 and moves
quickly; there are no maintained backport branches.

| Version | Supported |
|---|---|
| Latest release | Yes |
| Anything older | No — please upgrade |

## What is in scope

This software drives real hardware, so its security surface is not only the usual one.

**The local agent servers.** The MCP server (on by default) and the A2A server (opt-in)
listen on the loopback interface and can transmit on a connected CAN bus. They refuse
requests carrying an `Origin` header and requests whose `Host` is not the loopback
interface, which together block a web page the user happens to visit from reaching them.
A way around those guards is a vulnerability.

**The diagnostic write gate.** Writing to a vehicle passes a process-level switch
(`CANOPEN_STUDIO_DIAG_WRITE`), a per-profile whitelist, and an explicit per-call
confirmation — plus `CANOPEN_STUDIO_MCP_DIAG_WRITE` when the request arrives through MCP.
Anything that causes a write to be transmitted without all of those is a vulnerability.

**The decoding-formula interpreter.** Vehicle profiles and imported Torque/DBC files are
data supplied by the user. Their formulas are parsed to a syntax tree, checked node by
node against a whitelist, and evaluated by walking that tree; nothing is compiled and
`eval` is never called. Any input that reaches the filesystem, the network, the
interpreter, or that hangs or exhausts memory, is a vulnerability.

**File loading.** Profiles are read with `yaml.safe_load`. Anything that executes code or
escapes the intended parse from a profile, a DBC or a CSV is a vulnerability.

**The in-app updater.** It downloads releases and can launch an installer. Anything that
causes it to fetch or run something other than the intended release is a vulnerability.

## What is not a vulnerability

These are known, documented properties rather than defects. Reporting them is welcome as
a discussion, but they will not be treated as advisories.

- **The local servers have no authentication.** Any process already running as the same
  user on the same machine can reach them. The `Host` and `Origin` guards exist to keep
  *web pages* out, not other local processes. Use `CANOPEN_STUDIO_MCP=0` on a shared
  machine.
- **A CAN bus has no authentication either.** Anything with electrical access to the bus
  can transmit on it. That is a property of CAN, not of this software.
- **Bridging and injection do what they say.** `bridge_start(allow_inject=True)` replays
  network frames onto real hardware, and raising `CANOPEN_UDP_HOP_LIMIT` lets a virtual
  bus cross routers. Both are off or conservative by default and documented.
- **Enabling diagnostic writes is an operator decision.** Running with
  `CANOPEN_STUDIO_DIAG_WRITE=1` and `CANOPEN_STUDIO_MCP_DIAG_WRITE=1` deliberately allows
  a connected AI agent to clear trouble codes — and so to erase a vehicle's readiness
  monitors. The capability is announced at startup and in every relevant tool result.
- **A vehicle profile you chose to install is trusted to be wrong, not to be safe.** A
  bad profile can decode a parameter incorrectly. It cannot execute code; if you find one
  that can, that *is* a vulnerability.

## Safety, which is not the same thing

Bugs here can have physical consequences. Please keep that in mind when testing:

- The studio can transmit on a live bus. On a vehicle or a machine, that can move
  something.
- Clearing diagnostic trouble codes erases the readiness monitors. The vehicle needs a
  full drive cycle to rebuild them, and an emissions inspection taken before then fails.
- Reproduce against the built-in virtual bus, the UDP multicast bus, or the ELM327
  emulator wherever you can. Use a vehicle only when nothing else will show the problem,
  and never one that is moving.
