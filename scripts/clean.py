"""Remove build artifacts, caches and captured CSV traces.

Lives here rather than inline in the justfile: a `#!/usr/bin/env python3`
recipe cannot run on Windows, where `env` is not on PATH.
"""

import glob
import os
import shutil

for path in ["build", "dist", "site"] + glob.glob("*.csv"):
    if not os.path.exists(path):
        continue
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)

for root, dirs, _files in os.walk("."):
    for name in dirs:
        if name in ("__pycache__", ".pytest_cache"):
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
