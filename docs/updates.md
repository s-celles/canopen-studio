# In-App Updates & Maintenance

CAN & CANopen Studio includes an integrated update client for checking, downloading, and applying new versions directly from the user interface.

---

## 1. Automatic Background Verification

- **Non-blocking Startup**: On launch, the application checks `https://api.github.com/repos/s-celles/canopen-studio/releases/latest` in a lightweight daemon thread.
- **Visual Alert**: If a newer release is detected, the status bar at the bottom displays:
  `CANopen Studio v0.5.0 — ⚡ New release v0.6.0 available!`
  alongside a direct `[🚀 Update to v0.6.0]` action button.

---

## 2. Manual Verification

At any time, navigate to the top menu:
**Help → Check for Updates...**

A dialog opens detailing:
- Currently installed version
- Latest version available on GitHub
- Full Markdown release notes from GitHub Releases

---

## 3. One-Click Upgrading

### For Windows Setup Users (.exe)
- Click **📥 Download & Install**.
- The installer executable (`CANopen-Studio-Setup.exe`) downloads into your local `%TEMP%` folder with a progress bar.
- The application prompts for confirmation, terminates gracefully, and launches the Windows Setup Wizard to update the installation in `C:\Program Files\CANopen Studio`.

### For Source / Git Developers
- If `.git` is detected in the project root, the dialog offers an **🔄 Update via Git & uv** button.
- Runs `git pull` followed by `uv sync` in the background and informs you upon completion.
