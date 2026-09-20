{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = [
    # Provide Python 3.13 with Tkinter compiled in and linked correctly
    (pkgs.python313.withPackages (ps: [ ps.tkinter ]))
    # Fast Python dependency manager
    pkgs.uv
    # Rust toolchain & build tools for high-performance canopen-core
    pkgs.cargo
    pkgs.rustc
    pkgs.rustfmt
    pkgs.clippy
    pkgs.maturin
    pkgs.pkg-config
    pkgs.just
    # Required by canopen-gui (Slint) at build time
    pkgs.fontconfig
    # Required by Slint/winit at runtime: Wayland + input + GPU
    pkgs.wayland
    pkgs.libxkbcommon
    pkgs.mesa
    pkgs.libGL
    # Required for python-can / hardware interfaces on Linux
    pkgs.linuxHeaders
    # Provides libstdc++.so.6 needed by binary wheels (numpy, matplotlib, etc.)
    pkgs.stdenv.cc.cc.lib
  ];

  shellHook = ''
    # Force uv to use the Nix-provided Python instead of downloading its own standalone builds
    # (Standalone builds from uv often fail to find libX11/libtk on NixOS)
    export UV_PYTHON_PREFERENCE=system

    # Allow binary wheels (numpy, matplotlib) to find libstdc++.so.6
    # Allow Slint/winit to find Wayland, libxkbcommon, libGL at runtime
    export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:${pkgs.wayland}/lib:${pkgs.libxkbcommon}/lib:${pkgs.mesa}/lib:${pkgs.libGL}/lib:$LD_LIBRARY_PATH"

    # Expose _tkinter to the uv venv: on NixOS it lives in site-packages (ps.tkinter),
    # not lib-dynload, so the venv needs --system-site-packages to pick it up.
    if ! grep -q "^include-system-site-packages = true" .venv/pyvenv.cfg 2>/dev/null; then
      echo "==> Recreating venv with --system-site-packages (NixOS tkinter fix)..."
      uv venv --python "$(which python)" --system-site-packages --quiet
      uv sync --quiet
    fi

    echo "NixOS environment loaded for CANopen Studio!"
    echo "Python version: $(python --version)"
    echo "Run 'uv sync' then 'just gui'"
  '';
}
