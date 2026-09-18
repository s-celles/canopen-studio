{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = [
    # Provide Python 3.13 with Tkinter compiled in and linked correctly
    (pkgs.python313.withPackages (ps: [ ps.tkinter ]))
    # Fast Python dependency manager
    pkgs.uv
    # Required for python-can / hardware interfaces on Linux
    pkgs.linuxHeaders
  ];

  shellHook = ''
    # Force uv to use the Nix-provided Python instead of downloading its own standalone builds
    # (Standalone builds from uv often fail to find libX11/libtk on NixOS)
    export UV_PYTHON_PREFERENCE=system
    
    echo "NixOS environment loaded for CANopen Studio!"
    echo "Python version: $(python --version)"
    echo "Run 'uv sync' then 'just gui'"
  '';
}
