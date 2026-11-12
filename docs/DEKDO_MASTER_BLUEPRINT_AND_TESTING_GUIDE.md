# Master Specification & Build Blueprint: DekDo (ESP32 USB Desktop Control Hub)

> **Architecture Target**: Cable-Only USB Serial Controller for Windows 11 / 10.  
> **Operational Paradigm**: Runs as an invisible, self-starting background Windows service (Driverless Feel). The GUI app exists strictly on-demand for button/joystick customization and live calibration.

---

## 1. System Overview & Technical Architecture

```
+----------------------------------------------------------------------------------+
|                            DEKDO HARDWARE HUB                                    |
|                                                                                  |
|   [4x Tactile Buttons] ----> GPIO 13, 14, 25, 26 (Active LOW with Pullup)        |
|   [Analog 2-Axis Stick] ---> VRx (GPIO 34), VRy (GPIO 35) [3.3V Only]            |
|   [Joystick Push SW] ------> GPIO 27 (Active LOW with Pullup)                    |
|   [Active/Piezo Buzzer] ---> GPIO 18 (PWM / Tone Engine)                         |
|                                                                                  |
|                ESP32 DevKit V1 (WROOM-32) Microcontroller                        |
|                     Onboard USB-UART Chip (CP2102 / CH340)                       |
+----------------------------------------+-----------------------------------------+
                                         |
                                         | Micro-USB to USB-A / USB-C Cable
                                         | 115200 Baud ASCII Serial Stream
                                         v
+----------------------------------------------------------------------------------+
|                              WINDOWS LAPTOP (PC)                                 |
|                                                                                  |
|  +----------------------------------------------------------------------------+  |
|  |             LAYER 1: SILENT BACKGROUND ENGINE (DekDo.exe / daemon)         |  |
|  |  - Auto-starts on Windows login via HKCU Startup Registry                  |  |
|  |  - Auto-scans and binds to ESP32 COM port on cable plug-in                 |  |
|  |  - Parses Serial Stream: J,x,y (Joystick) | B,id,st (Buttons) | S,st       |  |
|  |  - Dispatches to OS APIs: pycaw (Audio) | sbc (Brightness) | SendInput    |  |
|  |  - Consumes < 25 MB RAM and 0.0% idle CPU; silent reconnect on plug/unplug|  |
|  +----------------------------------------------------------------------------+  |
|                                        ^                                         |
|                                        | Reads / Hot-Reloads                     |
|                                        v                                         |
|  +----------------------------------------------------------------------------+  |
|  |             LAYER 2: ON-DEMAND CUSTOMIZATION GUI (Configurator)            |  |
|  |  - Launched ONLY when user wants to remap keys or adjust sensitivity      |  |
|  |  - Real-time hardware telemetry (interactive crosshair & button badges)   |  |
|  |  - Dropdown hotkey picker, macro recorder, deadzone & speed sliders        |  |
|  |  - Saves directly to config.json; immediately shuts down when done         |  |
|  +----------------------------------------------------------------------------+  |
+----------------------------------------------------------------------------------+
```

---

## 2. Bill of Materials (BOM) & Pinout Matrix

| Component | Quantity | Connection on ESP32 DevKit | Operating Voltage | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **ESP32 DevKit V1 (30-pin WROOM-32)** | 1 | Micro-USB to PC | 5V via USB | USB-UART onboard |
| **Tactile Push Buttons** | 4 | GPIO 13, 14, 25, 26 -> Other pin to GND | 3.3V Logic | Internal `INPUT_PULLUP` enabled |
| **Analog 2-Axis Thumb Joystick** | 1 | VCC -> 3V3, GND -> GND | **3.3V STRICTLY** | Never connect to 5V (damages ADC) |
| - Joystick X-Axis (VRx) | - | GPIO 34 (ADC1_CH6) | Analog (0 - 3.3V) | Input-only pin |
| - Joystick Y-Axis (VRy) | - | GPIO 35 (ADC1_CH7) | Analog (0 - 3.3V) | Input-only pin |
| - Joystick Push Switch (SW) | - | GPIO 27 -> Other pin to GND | 3.3V Logic | Internal `INPUT_PULLUP` enabled |
| **Piezo / Active Buzzer** | 1 | (+) to GPIO 18, (-) to GND | 3.3V Logic | Driven via PWM / `tone()` |
| **Micro-USB Data Cable** | 1 | DevKit port to Laptop USB-A/C | 5V Power + Data | Must be a **data** cable, not charge-only |

---

## 3. Communication Protocol (Serial Framing)

The communication over the USB cable runs at **115200 baud, 8-N-1**:

### Device to Computer (Transmitted every 20ms or on edge change)
* **Joystick Position**: `J,<x>,<y>\n`
  * Example: `J,1812,1805\n` (12-bit ADC values: 0 to 4095, center ~1800-2048)
* **Button Edge Event**: `B,<button_id>,<state>\n`
  * Example: `B,1,1\n` (Button 1 Pressed), `B,1,0\n` (Button 1 Released)
  * Button IDs: `1` (GPIO 13), `2` (GPIO 14), `3` (GPIO 25), `4` (GPIO 26)
* **Switch Click Event**: `S,<state>\n`
  * Example: `S,1\n` (Stick Pressed), `S,0\n` (Stick Released)

### Computer to Device (Acoustic Feedback Commands)
* `BEEP_CONNECT\n`: Plays two-tone welcome chime on successful USB handshake.
* `BEEP_LIMIT\n`: Plays rapid double-beep when volume hits 0% or 100%.
* `BEEP_MUTE_ON\n`: Low descending chirp (Audio muted).
* `BEEP_MUTE_OFF\n`: High ascending chirp (Audio unmuted).
* `BEEP_UNLOCK\n`: 4-note ascending fanfare when laptop unlock sequence completes.

---

## 4. Hardware Step-by-Step Construction & Verification

### Step 1: Physical Wiring
1. Connect all common grounds (Buttons GND, Joystick GND, Buzzer (-)) to an ESP32 `GND` pin.
2. Connect Joystick `VCC` pin to ESP32 `3V3` pin.
3. Wire Button 1, 2, 3, 4 signal legs to GPIO 13, 14, 25, 26.
4. Wire Joystick VRx to GPIO 34, VRy to GPIO 35, SW to GPIO 27.
5. Wire Buzzer (+) leg to GPIO 18.

### Step 2: Firmware Installation (Arduino IDE)
1. In Arduino IDE Settings, add ESP32 board manager: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`.
2. Select Board: **ESP32 Dev Module**.
3. Flash the firmware (`desktop_control_hub_esp32.ino`).

---

## 5. Software Implementation (Background Engine & GUI Configurator)

### Background Engine Responsibilities
1. **Auto-Discovery**: Continuously scans Windows COM ports using `pyserial.tools.list_ports`. Matches ESP32 VID/PID (Silicon Labs CP210x `10C4:EA60` or WCH CH340 `1A86:7523`) or falls back to port specified in `config.json`.
2. **Audio Volume & Mute**: Uses Windows Core Audio API via `pycaw`. Changes master volume in 2% increments with throttling (minimum 60ms between steps) to prevent OS queue saturation.
3. **Brightness Control**: Calls `screen_brightness_control` for non-blocking laptop backlight adjustments.
4. **Key Dispatching**: Direct synthesis of scan-codes using `pyautogui` / `SendInput` for zero dropped keystrokes.
5. **Config Hot-Reloading**: Monitors `config.json` timestamp; reloads new mappings immediately without restarting the background service.

### Configurator Responsibilities
1. Single-instance Qt interface (`PySide6`).
2. Interactive graphic visualizer showing live physical joystick position in real-time.
3. Profiles management and export/import.
4. Autostart registry toggle (`SOFTWARE\Microsoft\Windows\CurrentVersion\Run`).

---

## 6. Comprehensive Testing Plan & Verification Cases

### Phase 1: Hardware Integrity & Sensor Testing
| Test ID | Test Scenario | Expected Outcome | Pass/Fail Criteria |
| :--- | :--- | :--- | :--- |
| **HW-01** | Power-on Voltage Rail Check | Multimeter reads 3.28V - 3.32V between 3V3 and GND pins. | Must never exceed 3.3V on GPIO 34/35. |
| **HW-02** | Button Continuity Check | Pressing buttons 1-4 pulls GPIOs 13, 14, 25, 26 to 0V (GND). | Logic 1 (3.3V) unpressed, Logic 0 (0V) pressed. |
| **HW-03** | Joystick Neutral Center Test | Serial monitor outputs `J,x,y` with values between 1600 and 2200 when untouched. | Values must remain stable within $\pm 25$ ADC counts. |
| **HW-04** | Joystick Full Travel Range | Pushing stick to all 4 physical extremes reaches near 0 (< 100) and near 4095 (> 3900). | Full dynamic range verified on both axes. |
| **HW-05** | Piezo Buzzer Acoustic Test | On startup or serial command `BEEP_CONNECT`, buzzer produces audible 1760Hz -> 2637Hz chime. | Crisp, audible click/tone without static. |

### Phase 2: Serial Stream & Communication Testing
| Test ID | Test Scenario | Expected Outcome | Pass/Fail Criteria |
| :--- | :--- | :--- | :--- |
| **COMM-01** | Port Handshake at 115200 | Serial monitor receives clean `J,x,y` packets at 50Hz (every 20ms). | Zero corrupted packets or unreadable chars. |
| **COMM-02** | Switch Debounce Test | Pressing tactile button rapidly 10 times sends exactly 10 press and 10 release events. | No contact bounce / ghost clicks. |
| **COMM-03** | Cable Disconnection Recovery | Unplugging USB cable during operation and plugging back in resumes data flow automatically. | PC engine reconnects in < 1.5 seconds. |

### Phase 3: Software Background Engine & OS Integration
| Test ID | Test Scenario | Expected Outcome | Pass/Fail Criteria |
| :--- | :--- | :--- | :--- |
| **SW-01** | Silent Startup Execution | PC boots up; DekDo background engine starts silently without opening any GUI window. | `DekDo.exe` appears in Task Manager with 0% CPU. |
| **SW-02** | Master Volume Step Control | Nudging joystick Right steps Windows volume up by +2%; Left steps down by -2%. | Native Windows on-screen volume HUD responds. |
| **SW-03** | Volume Boundary Chimes | Decreasing volume to 0% or increasing to 100% triggers hardware `BEEP_LIMIT` buzzer alert. | Buzzer plays double-beep at limits. |
| **SW-04** | Instant Master Mute Toggle | Pressing the joystick thumb switch toggles master mute and sends `BEEP_MUTE_ON/OFF`. | Windows audio icon displays mute indicator. |
| **SW-05** | Hotkey Macro Execution | Pressing Button 1, 2, 3, 4 triggers mapped hotkeys (e.g. `Ctrl+C`, `Ctrl+V`, `Win+Shift+S`). | Target action executes instantly in foreground app. |
| **SW-06** | Screen Brightness Control | Pushing joystick Up/Down increases/decreases laptop monitor brightness by 5%. | Display backlight visibly adjusts. |

### Phase 4: GUI Configurator & Customization Workflow
| Test ID | Test Scenario | Expected Outcome | Pass/Fail Criteria |
| :--- | :--- | :--- | :--- |
| **GUI-01** | On-Demand Launch | User double-clicks DekDo app; Configurator window opens smoothly in dark mode. | Telemetry indicator displays "Connected (USB Cable)". |
| **GUI-02** | Live Hardware Visualizer | Moving joystick moves on-screen crosshair in real-time; pressing buttons turns badges green. | Real-time visual feedback confirms hardware state. |
| **GUI-03** | Remapping & Hot Reload | User reassigns Button 1 to `Win+D` and clicks "Save Configuration". Close GUI. | Button 1 now minimizes all windows immediately. |
| **GUI-04** | Dead-Zone Slider Tuning | Increasing dead-zone slider to 0.25 prevents minor joystick play from triggering volume. | Zero accidental triggers around center. |
