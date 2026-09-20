#!/bin/sh
# Launch the Python studio.
#
# Lives here rather than inline in the justfile so the NixOS workaround stays
# readable: a multi-line recipe would need a `#!` shebang, which cannot run on
# Windows, and folding it onto one line makes it unreadable.
#
# NixOS: the binary wheels (numpy, matplotlib) need libstdc++.so.6 from gcc-lib
# on LD_LIBRARY_PATH. The probe is a no-op anywhere else, where nix-store is
# absent.
if command -v nix-store >/dev/null 2>&1; then
    STDCXX=$(find /nix/store -maxdepth 3 -name "libstdc++.so.6" \
        -path "*/gcc-*-lib/lib/*" 2>/dev/null | head -1 | xargs -r dirname)
    if [ -n "$STDCXX" ]; then
        LD_LIBRARY_PATH="${STDCXX}:${LD_LIBRARY_PATH:-}"
        export LD_LIBRARY_PATH
    fi
fi

exec uv run canopen-studio
