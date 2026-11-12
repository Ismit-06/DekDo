# DekDo: Tactile Desktop Control Hub (ESP32 USB Serial + Windows 11 Fluent Suite)

[![Hardware: ESP32 DevKit V1](https://img.shields.io/badge/Hardware-ESP32%20WROOM--32-blue?style=flat-square&logo=espressif)](https://www.espressif.com/)
[![Windows 11 Fluent Suite](https://img.shields.io/badge/Platform-Windows%2011%20%7C%2010-0078D4?style=flat-square&logo=windows)](https://microsoft.com)
[![Protocol: 115200 Baud USB Serial](https://img.shields.io/badge/Interface-USB%20Serial%20(CP2102)-green?style=flat-square)](https://silabs.com)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-yellow?style=flat-square&logo=python)](https://python.org)
[![Qt: PySide6](https://img.shields.io/badge/GUI-PySide6%20Fluent-41CD52?style=flat-square&logo=qt)](https://qt.io)

An embedded systems & desktop integration project that turns an **ESP32 DevKit** into a physical macro control deck and multimedia hub over a single high-speed USB data cable. Features direct linear OS audio control, non-blocking display backlighting, instant keystroke macros, acoustic buzzer feedback, and custom Windows 11 Fluent On-Screen Displays (OSD).

---

## ⚡ System Architecture

```
+-------------------------------------------------------------------------------+
|                            DEKDO HARDWARE HUB                                 |
|                                                                               |
|   [4x Tactile Buttons] ----> GPIO 13, 14, 25, 26 (Debounced Active-LOW)       |
|   [Analog 2-Axis Stick] ---> VRx (GPIO 34), VRy (GPIO 35) [3.3V ADC]          |
|   [Thumb Push Switch] -----> GPIO 27 (Debounced Active-LOW)                   |
|   [Active/Piezo Buzzer] ---> GPIO 18 (PWM Frequency Engine)                   |
|                                                                               |
|                   ESP32 DevKit V1 (30-pin WROOM-32)                           |
|                 Onboard Silicon Labs CP2102 USB Bridge                        |
+---------------------------------------+---------------------------------------+
                                        |
                                        | High-Speed Micro-USB Cable
                                        | 115200 Baud Serial Stream
                                        v
+-------------------------------------------------------------------------------+
|                             WINDOWS LAPTOP / PC                               |
|                                                                               |
|  +-------------------------------------------------------------------------+  |
|  |                     DEKDO COMMAND SUITE (DekDo.exe)                     |  |
|  |                                                                         |  |
|  |  • Direct Core Audio Engine (pycaw): Linear master volume adjustment    |  |
|  |  • Asynchronous Backlight Queue (sbc): Non-blocking brightness thread   |  |
|  |  • Windows 11 Fluent Flyout (Qt): Custom volume & brightness OSD pills  |  |
|  |  • Fast Macro Dispatcher (pyautogui): Zero-latency scan-code synthesis  |  |
|  |  • Background Tray & Auto-Reconnect: Silent background operation       |  |
|  +-------------------------------------------------------------------------+  |
+-------------------------------------------------------------------------------+
```

---

## 🛠️ Bill of Materials & Wiring Matrix

All sensors and switches connect strictly to the **3.3V rail** (never 5V).

| Component | Pin / GPIO | Connection | Logic State / Notes |
| :--- | :--- | :--- | :--- |
| **Microcontroller** | ESP32 DevKit V1 | Micro-USB to PC | 5V Power from USB, 3.3V logic |
| **Button 1** | **GPIO 13** | Leg A -> GPIO 13, Leg B -> GND | Internal Pull-Up, Active LOW |
| **Button 2** | **GPIO 14** | Leg A -> GPIO 14, Leg B -> GND | Internal Pull-Up, Active LOW |
| **Button 3** | **GPIO 25** | Leg A -> GPIO 25, Leg B -> GND | Internal Pull-Up, Active LOW |
| **Button 4** | **GPIO 26** | Leg A -> GPIO 26, Leg B -> GND | Internal Pull-Up, Active LOW |
| **Joystick X-Axis** | **GPIO 34 (VRx)** | Signal -> GPIO 34, VCC -> 3V3, GND -> GND | Analog 12-bit ADC (0–4095) |
| **Joystick Y-Axis** | **GPIO 35 (VRy)** | Signal -> GPIO 35, VCC -> 3V3, GND -> GND | Analog 12-bit ADC (0–4095) |
| **Joystick Push SW** | **GPIO 27 (SW)** | Leg A -> GPIO 27, Leg B -> GND | Internal Pull-Up, Active LOW |
| **Piezo Buzzer** | **GPIO 18** | (+) -> GPIO 18, (-) -> GND | PWM / Tone generation |

---

## 📡 Serial Protocol Framing (115200 Baud, 8-N-1)

### Hardware to PC Stream
* **`J,<x>,<y>\n`** — Analog joystick position every 20ms (e.g., `J,1812,1805`).
* **`B,<id>,<state>\n`** — Tactile push button transition (`1` = pressed, `0` = released).
* **`S,<state>\n`** — Joystick center thumb switch click (`1` = pressed, `0` = released).

### PC to Hardware Commands
* **`BEEP_CONNECT\n`** — Welcome 2-tone chime upon successful USB connection handshake.
* **`BEEP_LIMIT\n`** — Double-beep warning when master volume hits 0% or 100%.
* **`BEEP_MUTE_ON\n`** — Low descending acoustic tone on audio mute.
* **`BEEP_MUTE_OFF\n`** — High ascending acoustic tone on audio unmute.
* **`BEEP_UNLOCK\n`** — 4-note musical fanfare on PC quick unlock.

---

## 🚀 Getting Started & Deployment

### 1. Flash the ESP32 Firmware
1. Open the [Arduino IDE](https://www.arduino.cc/en/software).
2. Open `desktop_control_hub_esp32/desktop_control_hub_esp32.ino`.
3. Set board to **ESP32 Dev Module** and select your Silicon Labs CP210x COM port.
4. Click **Upload (→)**.

### 2. Run the Windows Application
Simply run [Open_DekDo.bat](file:///Open_DekDo.bat) or [DekDo.exe](file:///DekDo.exe).
* When plugged in, the status badge turns **● Connected**.
* Live hardware telemetry immediately visualizes joystick coordinates and button presses.
* Custom hotkeys and macros can be mapped and saved directly inside the GUI.
