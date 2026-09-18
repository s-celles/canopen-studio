"""
Build script for compiling CAN & CANopen Studio into standalone Windows executables via PyInstaller.

Usage:
    uv run --with pyinstaller python scripts/build_exe.py [--onefile]
"""

import os
import sys
import shutil
import subprocess
import argparse


def main():
    parser = argparse.ArgumentParser(description="Build standalone executable with PyInstaller")
    parser.add_argument("--onefile", action="store_true", help="Build a single standalone .exe instead of a directory")
    parser.add_argument("--clean", action="store_true", help="Clean build directories before building")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)

    # Ensure icons exist
    icon_path = os.path.join(project_root, "assets", "icon.ico")
    if not os.path.exists(icon_path):
        print("Generating icon first...")
        subprocess.run([sys.executable, os.path.join(project_root, "scripts", "generate_icon.py")], check=True)

    dist_dir = os.path.join(project_root, "dist")
    build_dir = os.path.join(project_root, "build")

    if args.clean:
        if os.path.exists(dist_dir):
            shutil.rmtree(dist_dir)
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)

    # Base PyInstaller command
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--windowed",
        "--name=CANopen-Studio",
        f"--icon={icon_path}",
        # Assets & metadata
        f"--add-data={os.path.join('assets', 'icon.ico')}{os.pathsep}assets",
        f"--add-data={os.path.join('assets', 'icon.png')}{os.pathsep}assets",
        f"--add-data=LICENSE{os.pathsep}.",
        f"--add-data=README.md{os.pathsep}.",
        # Hidden imports for dynamic CAN interfaces and decoders
        "--hidden-import=serial",
        "--hidden-import=serial.tools.list_ports",
        "--hidden-import=can.interfaces.slcan",
        "--hidden-import=can.interfaces.virtual",
        "--hidden-import=can.interfaces.pcan",
        "--hidden-import=can.interfaces.kvaser",
        "--hidden-import=can.interfaces.vector",
        "--hidden-import=can.interfaces.ixxat",
        "--hidden-import=can.interfaces.gs_usb",
        "--hidden-import=can.interfaces.socketcan",
        "--hidden-import=can.interfaces.udp_multicast",
        "--hidden-import=msgpack",
        "--hidden-import=matplotlib.backends.backend_tkagg",
        "--hidden-import=canopen",
        "--collect-submodules=canopen_studio",
        # Source entry
        "src/canopen_studio/gui.py",
    ]

    if args.onefile:
        cmd.insert(3, "--onefile")
    else:
        cmd.insert(3, "--onedir")

    print("Running PyInstaller with command:")
    print(" ".join(cmd))
    print("-" * 60)

    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("-" * 60)
        print("BUILD SUCCESSFUL!")
        if args.onefile:
            exe_path = os.path.join(dist_dir, "CANopen-Studio.exe")
            print(f"Standalone executable created: {exe_path}")
        else:
            folder_path = os.path.join(dist_dir, "CANopen-Studio")
            print(f"Standalone application folder created: {folder_path}")
            print(f"Executable: {os.path.join(folder_path, 'CANopen-Studio.exe')}")
    else:
        print("BUILD FAILED!")
        sys.exit(res.returncode)


if __name__ == "__main__":
    main()
