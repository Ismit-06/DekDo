# Master Implementation & Architecture Refinement Prompt: DekDo (Clean Architecture)

You are an expert embedded systems firmware engineer, Windows desktop systems developer, and clean-code architect.
Your mission is to refine, clean up, and solidify the **DekDo Desktop Control Hub** project. 

The hardware uses an **ESP32 DevKit V1 (with onboard CP2102 USB-to-UART bridge)** connected strictly via a **Micro-USB cable** to a Windows 10/11 laptop.

---

### CORE ARCHITECTURAL PRINCIPLES

1. **Hardware-Owned Configuration (Stored in ESP32 NVS/EEPROM)**:
   - Button shortcuts (e.g. `win+shift+s`, `ctrl+c`, `ctrl+v`, `win+d`) and joystick mappings must be stored permanently in the **ESP32's non-volatile memory (NVS / Preferences)**.
   - The device owns its own identity and keybinds. If unplugged and moved to another machine, the hardware retains its assignments.

2. **Zero Clutter & Minimal Clean Codebase**:
   - Eliminate obsolete files, duplicate directories, dead artifacts, and legacy experimental code.
   - The entire project must consist of:
     - `firmware/` : Exactly one clean, robust Arduino `.ino` sketch for the ESP32.
     - `app/` : The streamlined Windows companion suite (`main.py`) containing the silent background daemon and on-demand Studio GUI.
     - `install_service.bat` / `uninstall_service.bat` : Clean scripts for Windows silent background startup.
     - `config.json` : Local cache / fallback profile.

3. **Two Distinct Operational Modes for the Desktop Software**:
   - **Mode A: Invisible Background Driver (`--daemon` mode)**:
     - Runs silently on Windows startup (0% idle CPU, no window, no popups).
     - Auto-detects ESP32 COM port over USB cable in < 1 second.
     - Receives execution packets from ESP32 over serial (e.g., `ACTION,KEY,win+shift+s` or `ACTION,VOL_UP`) and immediately triggers Windows OS APIs (`SendInput`, `pycaw`, `screen_brightness_control`).
   - **Mode B: On-Demand DekDo Studio Configurator (GUI mode)**:
     - Opened ONLY when the user wishes to remap buttons or adjust sensitivity/dead-zone.
     - Connects to the ESP32, reads current mappings directly from ESP32 flash memory (`GET_CONFIG`), allows graphical customization, and burns new bindings to ESP32 flash (`SET_CONFIG`).
     - Once saved, the user closes the Studio window. The silent daemon immediately hot-reloads and continues operation.

---

### STEP 1: CLEANUP & PURGE OBSOLETE FILES

Remove all redundant, duplicate, and leftover test files:
- Delete dummy zero-byte files (e.g., `=`).
- Delete `desktop_control_hub_esp32_ble/` (Bluetooth is completely excluded from this wired USB design).
- Delete redundant build directories (`build/`, `dist/`, `scratch/`, `__pycache__/`) which can be regenerated cleanly on build.
- Standardize directory structure:
  ```
  desktop_control_hub/
  ├── firmware/
  │   └── dekdo_firmware.ino
  ├── app/
  │   ├── main_app.py
  │   ├── config.json
  │   └── requirements.txt
  ├── install_service.bat
  ├── uninstall_service.bat
  └── README.md
  ```

---

### STEP 2: FIRMWARE REFACTOR (`firmware/dekdo_firmware.ino`)

The firmware must:
1. **Include `#include <Preferences.h>`** to store key mappings in ESP32 Flash (NVS partition `dekdo-config`).
2. **Default Keybinds stored in NVS**:
   - Button 1 (GPIO 13): `"win+shift+s"` (Screenshot tool)
   - Button 2 (GPIO 14): `"ctrl+c"` (Copy)
   - Button 3 (GPIO 25): `"ctrl+v"` (Paste)
   - Button 4 (GPIO 26): `"win+d"` (Show Desktop)
   - Joystick Click (GPIO 27): `"mute"` (Toggle Volume Mute)
   - Joystick X+ / X- : `"vol_up"` / `"vol_down"`
   - Joystick Y+ / Y- : `"bright_up"` / `"bright_down"`
3. **Serial Stream Protocol (115200 Baud)**:
   - On button press: Transmits execution request:  
     `EXEC,KEY,<shortcut_string>\n` (e.g., `EXEC,KEY,win+shift+s\n`)
   - On joystick displacement beyond dead-zone: Transmits every 40ms:  
     `EXEC,MEDIA,<vol_up|vol_down|bright_up|bright_down>\n`
   - Telemetry stream (only when Studio GUI requests it via `START_TELEMETRY`):  
     `TEL,x,y,b1,b2,b3,b4,sw\n`
4. **Configuration Protocol with DekDo Studio**:
   - `CMD,GET_CONFIG\n` -> Responds with JSON or delimited packet of all current NVS stored shortcuts.
   - `CMD,SET_BTN,<id>,<action_string>\n` -> Writes new shortcut into ESP32 NVS flash and plays confirmation beep.
   - `CMD,SET_DEADZONE,<float_val>\n` -> Saves dead-zone threshold in NVS.
5. **Acoustic Feedback (Buzzer on GPIO 18)**:
   - Unique pitch tone on each physical button press.
   - Musical chirp on USB connection handshake and flash write confirmation.

---

### STEP 3: SOFTWARE REFACTOR (`app/main_app.py`)

Refactor `main_app.py` into two cleanly separated runtime classes sharing a single unified file:

1. **`DekDoEngine` (Daemon / Background Driver)**:
   - Command-line argument: `python main_app.py --daemon`
   - Scans serial ports for Silicon Labs CP2102 (`VID:PID 10C4:EA60`).
   - Threaded reader with auto-reconnection loop:
     - Handles `EXEC,KEY,<combo>` -> splits modifiers (`win`, `ctrl`, `shift`, `alt`) and keys, calls `pyautogui.hotkey(*keys)`.
     - Handles `EXEC,MEDIA,vol_up` / `vol_down` -> adjusts Windows endpoint volume via `pycaw`.
     - Handles `EXEC,MEDIA,bright_up` / `bright_down` -> adjusts laptop screen brightness via `screen_brightness_control`.
     - Handles `EXEC,MEDIA,mute` -> toggles master mute.
   - Runs silently with 0% CPU usage when idle; no GUI components initialized.

2. **`DekDoStudio` (Qt Configuration GUI)**:
   - Default when run without `--daemon`.
   - Single-instance mutex check (if already running, bring window to front).
   - Pauses the background daemon reading, establishes serial handshake, and sends `CMD,GET_CONFIG`.
   - Populates interactive visual UI:
     - Dropdown / Hotkey Recorder for Buttons 1 to 4 (e.g. typing any key combination records it).
     - Joystick axis assignment (Volume, Brightness, Scroll, Mouse, Media).
     - Dead-zone slider with live visual calibration crosshair.
     - **"Burn to Hardware" Button**: sends `CMD,SET_BTN` packets directly to the ESP32.
   - On window close: sends `STOP_TELEMETRY` and cleanly yields the COM port back to the background daemon.

3. **Service Scripts**:
   - `install_service.bat`: Adds `pythonw main_app.py --daemon` (or compiled `.exe --daemon`) to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` so it starts automatically with Windows invisibly.
   - `uninstall_service.bat`: Removes the startup entry cleanly.

---

### STEP 4: VERIFICATION & COMPREHENSIVE TEST MATRIX

Execute tests in sequence:
1. **TEST-01 (NVS Memory Persistence)**:
   - Configure Button 1 to `ctrl+shift+esc` in DekDo Studio. Click Burn to Hardware.
   - Unplug ESP32 USB cable, plug it back in.
   - Read config back; verify Button 1 is still `ctrl+shift+esc` stored on the ESP32.
2. **TEST-02 (Zero-UI Background Execution)**:
   - Run `--daemon`. Press Button 1; verify Task Manager opens immediately without any visible app window.
3. **TEST-03 (Screenshot Shortcut)**:
   - Press button assigned to `win+shift+s`; verify Windows Snipping Tool overlay activates instantly.
4. **TEST-04 (Volume & Limit Chime)**:
   - Push joystick right; verify Windows master volume increments smoothly.
   - At 100% volume, verify buzzer sounds double beep.
5. **TEST-05 (Laptop Brightness Step)**:
   - Push joystick up/down; verify laptop screen backlight increases/decreases.
6. **TEST-06 (Cable Hot-Plug Recovery)**:
   - Unplug USB cable while moving the joystick. Replug USB cable. Verify reconnection and normal operation resume in < 1.5 seconds.
