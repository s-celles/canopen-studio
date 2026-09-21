/*
 * CANopen Studio — Wireshark extcap entry point
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

//! Wireshark invokes an extcap as a bare executable with its own flags, so
//! this cannot be a subcommand of `canopen-cli`: it needs its own binary,
//! dropped into Wireshark's extcap directory.

#[path = "../extcap.rs"]
mod extcap;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Err(e) = extcap::run(&args) {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
