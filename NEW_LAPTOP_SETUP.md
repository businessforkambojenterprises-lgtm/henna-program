# Moving Ai+d405 to a new PC

Nothing on the old PC is moved or deleted - keep it until the new PC has passed Part C.
The new PC uses the same Windows user name (**DELL**), so old jobs reopen there too.

## The move package (made on the old PC by `make_move_package.py`)
Two zips on the old PC's Desktop:

| Zip | Inside `Ai+d405_move\` | Size |
|---|---|---|
| `Ai+d405_move_<date>_essentials.zip` | `Ai+d405\` the project (code, git history, calibration, results, jobs, hand model), `HennaStudio-Windows-x64\` the desktop app with its own jobs and log (old `backup-before-*` folders left out), `claude_project\` Claude's notes and chats, `Henna Studio.lnk`, `restore_on_new_pc.bat` | about 0.5 GB |
| `Ai+d405_move_<date>_recordings.zip` | `recordings\` the 7 scans from 10 Oct 2026 (Documents) - needed only to reopen those jobs | about 2 GB |

The older recordings (`D:\recordings`, 23 GB) stay on the old PC (history only). The GitHub copy
(https://github.com/businessforkambojenterprises-lgtm/henna-program) has the code but not the jobs, the hand
model, the desktop app or the recordings - that is why the package exists.

## Part A - old PC
1. Close Henna Studio, RealSense Viewer and UGS.
2. Make the package (Command Prompt):
   ```
   cd /d "%USERPROFILE%\Desktop\Ai+d405"
   .venv\Scripts\python.exe make_move_package.py
   ```
   It ends with "verified" for both zips.
3. Upload both zips to Google Drive / OneDrive, or copy them to a USB drive (exFAT or NTFS; FAT32 cannot hold the 2 GB zip).

## Part B - new PC
1. **Install** (all free): **Python 3.11.9 64-bit** from python.org (tick **"Add python.exe to PATH"**),
   **Git for Windows**, the **Claude desktop app** (same account). Optional: Intel RealSense Viewer 2.58.
   The CH340 USB driver only if the CNC does not appear as a COM port. UGS is not needed any more.
2. Download both zips. For each: right-click -> **Extract All** -> set the destination to
   `C:\Users\DELL\Desktop` -> Extract. Both land in `Desktop\Ai+d405_move`.
3. Open `Desktop\Ai+d405_move` and double-click **`restore_on_new_pc.bat`**. It copies the project to
   `Desktop\Ai+d405`, the app to `Desktop\HennaStudio-Windows-x64` (plus the shortcut), Claude's notes, and the
   recordings to `Documents`. It stops without copying if those folders already exist. All 5 steps must pass.
4. Double-click **`Desktop\Ai+d405\setup_new_laptop.bat`**: creates `.venv`, installs the exact package versions,
   checks the hand model (downloads it again if missing, SHA-256 checked) and runs all self-tests - must end
   with "Done - all tests OK".
5. Plug in the **D405 on a blue USB 3 port** and the **CNC USB** cable.
6. When Part C passed, `Desktop\Ai+d405_move` and the zips can be deleted on the new PC.

## Part C - first checks on the new PC
1. Start **Henna Studio** from the desktop shortcut (its own window) - or from source in a Command Prompt:
   ```
   cd /d "%USERPROFILE%\Desktop\Ai+d405"
   .venv\Scripts\python.exe webui_server_v22.py
   ```
   and open http://127.0.0.1:8765. Use one of them at a time (one user per device).
2. **Connect** (the CH340 COM port is found automatically), then **Home** (`$HZ`, then `$H`).
3. New job -> Prosthetic hand -> **Move to scan position** -> **Record**: the checks must pass with depth units
   0.0001 m.
4. The calibration belongs to the camera mount and the machine, not the computer: it stays valid only if the
   camera mount was not touched. First program on the new PC: an **air run with no hand under the machine**,
   then the prosthetic as usual.

## Claude on the new PC
- Open the Claude desktop app -> Code tab -> choose `Desktop\Ai+d405`. `CLAUDE.md` is read automatically and
  Claude's notes (memory) are already in place from the restore.
- GitHub: `git pull` / `git push` need you to sign in to GitHub once on the new PC (a browser window opens).
