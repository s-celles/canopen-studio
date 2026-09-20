"""Run the OS-specific installer.

Lives here rather than inline in the justfile: a `#!/usr/bin/env python3`
recipe cannot run on Windows, where `env` is not on PATH.
"""

import os
import subprocess
import sys

if os.name == "nt":
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "scripts/install_windows.ps1",
    ]
else:
    cmd = ["bash", "install.sh"]

sys.exit(subprocess.run(cmd).returncode)
