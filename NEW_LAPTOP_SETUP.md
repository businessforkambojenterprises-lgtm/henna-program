# Moving Ai+d405 to a new laptop

Everything that matters travels in one folder, `Ai+d405_transfer`, made by `transfer_copy.bat`.
Nothing on the old PC is moved or deleted - keep it until the new laptop has passed Part C.

| In the transfer folder | What it is | Size |
|---|---|---|
| `Ai+d405\` | the project: code, git history, calibration, results, jobs, hand model, this guide, `CLAUDE.md` | about 70 MB |
| `recordings_Documents\` | the camera recordings from Documents | about 23 GB |
| `recordings_archive\` | older recordings (history only) | about 29 GB |
| `claude_memory\` | Claude's saved notes for this project | tiny |
| `chat_export\` | the full Claude conversation (zip) | about 100 MB |

## No USB drive? Use the essentials zip (recommended)
`Desktop\Ai+d405_essentials.zip` (about 170 MB) holds the project, Claude's notes and the chat export -
everything except the recordings, which are history only (the new laptop makes new scans).
1. Old PC: open drive.google.com (or onedrive.live.com) and drag the zip into it.
2. New laptop: download it from the same account, right-click -> **Extract All** to the Desktop.
   Move the inner `Ai+d405` folder so it is `Desktop\Ai+d405`.
3. Continue with **Part B** below (skip the recording copies).
Recordings later, only if needed: share `Documents` over the home network (right-click -> Give access to)
or upload single files.

## Part A - on the OLD PC (with a USB drive or external disk)
1. Stop the studio (Ctrl+C in its Command Prompt), disconnect UGS, close RealSense Viewer.
2. Plug in a USB drive or external disk with **at least 60 GB free** (NTFS or exFAT).
3. Open a Command Prompt and type (replace `E:` with your drive's letter):
   ```
   cd /d "%USERPROFILE%\Desktop\Ai+d405"
   transfer_copy.bat E:
   ```
4. Wait (tens of minutes for 50 GB). Each of the 4 parts ends with a summary: **FAILED must be 0**.

## Part B - on the NEW laptop
1. **Install** (all free):
   - **Python 3.11.9, 64-bit** from python.org - tick **"Add python.exe to PATH"**.
   - **UGS** (Universal Gcode Sender, platform edition).
   - **Intel RealSense Viewer** 2.58 (optional, for checking the camera).
   - The **CH340 USB driver** only if the CNC does not show up as a COM port (Windows Update usually adds it).
   - The **Claude desktop app** (same account).
2. **Copy** from the USB drive:
   - `Ai+d405_transfer\Ai+d405`  ->  your **Desktop**, so it is `Desktop\Ai+d405`.
   - `recordings_Documents\*.db3`  ->  your **Documents** folder.
   - `recordings_archive\`  ->  `D:\d405_recordings_archive` if the laptop has a D: drive, else any folder.
3. In `Desktop\Ai+d405`, **double-click `setup_new_laptop.bat`**. It creates `.venv`, installs the exact
   package versions (`requirements-lock.txt`) and runs the self-tests - they must end with **OK**.
4. Plug in the **D405 on a USB 3 port** and the **CNC USB** cable.

## Part C - first checks on the new laptop
1. Start the studio in a Command Prompt (keep the window open):
   ```
   cd /d "%USERPROFILE%\Desktop\Ai+d405"
   .venv\Scripts\python.exe webui_server_v12.py
   ```
2. Open http://127.0.0.1:8765. **Connect** (the CH340 COM port is found automatically), then **Home**.
3. New job -> Prosthetic hand -> **Scan** at pose 1: the checks must show depth units 0.0001 m and
   about 29 camera settings.
4. Do one full prosthetic job with the paper readings. The calibration belongs to the camera mount and the
   machine, not to the computer, so it stays valid as long as the camera mount was not touched - the paper
   readings confirm it.

## Claude on the new laptop
- Open the project folder in the Claude desktop app (Code tab -> choose `Desktop\Ai+d405`).
  **`CLAUDE.md` is read automatically**: safety rules, workflow, key files and findings.
- Optional: copy `claude_memory\*` into `%USERPROFILE%\.claude\projects\<project key>\memory\`. The key
  is the folder path with `:\`, `\` and `+` replaced by `-`, e.g. `C--Users-NAME-Desktop-Ai-d405`.
- The old conversation is in `chat_export\` for reference; a new conversation starts fresh.

## Good to know
- Old jobs and scan records store full paths such as `C:\Users\DELL\Documents\...`. If the new Windows user
  name is also **DELL** and you use the same folders, old jobs can be resumed. Otherwise simply start new
  jobs - calibration, designs and all results still apply.
- `outputs\` and recordings are not in git on purpose (size); the transfer copies them anyway.
