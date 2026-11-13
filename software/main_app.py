import os
import sys
import time
import json
import threading
import subprocess
import winreg
import serial
from serial.tools import list_ports
import win32event
import win32api
import winerror

import pyautogui
import screen_brightness_control as sbc

# In Windows, comtypes must be initialized per-thread for audio pycaw
import comtypes
from pycaw.pycaw import AudioUtilities

from PySide6.QtCore import Qt, QThread, Signal, Slot, QPointF, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtNetwork import QLocalSocket, QLocalServer
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QPen, QFont, QLinearGradient
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSlider, QLineEdit, QTabWidget,
    QScrollArea, QFrame, QTextEdit, QProgressBar, QGroupBox, QGridLayout,
    QSystemTrayIcon, QMenu, QCheckBox, QMessageBox, QStyle, QGraphicsDropShadowEffect
)

# PyAutoGUI safety setting
pyautogui.FAILSAFE = False

# Path where configuration is preserved
def get_config_path():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "config.json")

CONFIG_PATH = get_config_path()

DEFAULT_CONFIG = {
    "port": "Auto-Detect",
    "baud_rate": 115200,
    "dead_zone": 0.15,
    "speed": 40.0,
    "calibrated_center_x": 1795.0,
    "calibrated_center_y": 1805.0,
    "auto_reconnect": True,
    "invert_x": False,
    "invert_y": False,
    "joystick_left": {"action": "Volume Down", "key": "", "cmd": ""},
    "joystick_right": {"action": "Volume Up", "key": "", "cmd": ""},
    "joystick_up": {"action": "Brightness Up", "key": "", "cmd": ""},
    "joystick_down": {"action": "Brightness Down", "key": "", "cmd": ""},
    "joystick_click": {"action": "Mute / Unmute Volume", "key": "", "cmd": ""},
    "buttons": {
        "1": {"name": "Button 1 (GPIO 13)", "action": "Unlock Laptop (Type Password)", "key": "", "cmd": ""},
        "2": {"name": "Button 2 (GPIO 14)", "action": "Custom Hotkey", "key": "win+shift+s", "cmd": ""},
        "3": {"name": "Button 3 (GPIO 25)", "action": "Custom Hotkey", "key": "ctrl+c", "cmd": ""},
        "4": {"name": "Button 4 (GPIO 26)", "action": "Custom Hotkey", "key": "ctrl+v", "cmd": ""}
    },
    "unlock_password": "",
    "unlock_button": "1"
}

JOYSTICK_ACTIONS = [
    "Volume Up",
    "Volume Down",
    "Brightness Up",
    "Brightness Down",
    "Scroll Up (Mouse Wheel)",
    "Scroll Down (Mouse Wheel)",
    "Mouse Cursor Move",
    "Media Next Track",
    "Media Previous Track",
    "Mute / Unmute Volume",
    "Unlock Laptop (Type Password)",
    "Custom Hotkey",
    "Launch Program / Command",
    "None"
]

BUTTON_ACTIONS = [
    "Unlock Laptop (Type Password)",
    "Custom Hotkey",
    "Launch Program / Command",
    "Media Play / Pause",
    "Media Next Track",
    "Media Previous Track",
    "Mute / Unmute Volume",
    "Task View (Win+Tab)",
    "Switch Windows (Alt+Tab)",
    "Show Desktop (Win+D)",
    "Open Task Manager (Ctrl+Shift+Esc)",
    "Lock Workstation (Win+L)",
    "None"
]

def load_app_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(data)
                return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_app_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=4)
        return True
    except Exception as e:
        return False


# ==============================================================
# HARDWARE SERIAL THREAD (ROBUST WITH AUTO-RECONNECT & COINIT)
# ==============================================================
from PySide6.QtCore import QObject

class SerialWorker(QObject):
    sig_connected = Signal(bool, str)
    sig_log = Signal(str)
    sig_joystick = Signal(int, int, float, float)
    sig_button = Signal(int, int)
    sig_switch = Signal(int)
    sig_vol_change = Signal(float)
    sig_bright_change = Signal(int)
    sig_calibrated = Signal(float, float)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._running = False
        self._thread = None
        self.serial_inst = None
        self.center_x = float(self.config.get("calibrated_center_x", 1795.0))
        self.center_y = float(self.config.get("calibrated_center_y", 1805.0))
        self.volume_ctrl = None
        self.cur_volume = 50.0
        self.cur_brightness = 50.0
        self.last_brightness_time = 0
        self.last_volume_time = 0
        self.calibrating = False
        self.calib_samples_x = []
        self.calib_samples_y = []
        self.calib_start = 0

        # Background worker for non-blocking brightness adjustment (prevents serial thread stall)
        self._brightness_queue = []
        self._brightness_lock = threading.Lock()
        self._brightness_worker_thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run_wrapper, daemon=True)
        self._thread.start()
        self._brightness_worker_thread = threading.Thread(target=self._brightness_worker_loop, daemon=True)
        self._brightness_worker_thread.start()

    def _brightness_worker_loop(self):
        """Asynchronous background worker that sets monitor brightness without stalling the serial parser loop."""
        while self._running:
            target_val = None
            with self._brightness_lock:
                if self._brightness_queue:
                    target_val = self._brightness_queue.pop()
                    self._brightness_queue.clear()
            if target_val is not None:
                try:
                    sbc.set_brightness(int(target_val))
                except Exception:
                    pass
            time.sleep(0.04)

    def isRunning(self):
        return bool(self._thread and self._thread.is_alive())

    def wait(self, timeout_ms=1000):
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout_ms / 1000.0)

    def _run_wrapper(self):
        try:
            self.run()
        except RuntimeError as e:
            # Catch Qt object deleted when shutting down
            if "Signal source has been deleted" in str(e) or not self._running:
                pass
            else:
                try:
                    self.sig_log.emit(f"[Thread Error] {e}")
                except Exception:
                    pass
        except Exception as e:
            if self._running:
                try:
                    self.sig_log.emit(f"[Thread Error] {e}")
                except Exception:
                    pass

    def init_audio(self):
        try:
            comtypes.CoInitialize()
            spk = AudioUtilities.GetSpeakers()
            self.volume_ctrl = spk.EndpointVolume
            self.cur_volume = self.volume_ctrl.GetMasterVolumeLevelScalar() * 100.0
            self.sig_vol_change.emit(self.cur_volume)
        except Exception as e:
            self.sig_log.emit(f"[Audio Error] {e}")

    def init_brightness(self):
        try:
            b = sbc.get_brightness()
            if b:
                self.cur_brightness = float(b[0])
                # Only initialize state without triggering the flyout overlay
                self.sig_vol_change.emit(self.cur_volume)
        except Exception:
            self.cur_brightness = 50.0

    def request_calibration(self):
        self.calib_samples_x = []
        self.calib_samples_y = []
        self.calib_start = time.monotonic()
        self.calibrating = True
        self.sig_log.emit(">>> Calibration running (1.5s): Please leave joystick resting at center...")

    def stop(self):
        self._running = False
        if self.serial_inst and self.serial_inst.is_open:
            try:
                self.serial_inst.close()
            except Exception:
                pass

    def normalize(self, val, center, invert=False):
        diff = (val - center) / 2048.0
        if invert:
            diff = -diff
        dz = float(self.config.get("dead_zone", 0.15))
        if abs(diff) < dz:
            return 0.0
        if diff > 0:
            return min(1.0, (diff - dz) / (1.0 - dz))
        return max(-1.0, (diff + dz) / (1.0 - dz))

    def execute_unlock(self):
        """Simulate waking the lock screen and typing the configured password / PIN via native Win32 events."""
        pwd = self.config.get("unlock_password", "")
        self.sig_log.emit(f"[Unlock] Executing unlock sequence (length {len(pwd)})...")
        self.send_device_command("BEEP_UNLOCK")
        
        def _type_unlock():
            time.sleep(0.15)
            # 1. Wake screen / dismiss screensaver with Space
            win32api.keybd_event(0x20, 0, 0, 0)
            win32api.keybd_event(0x20, 0, 2, 0)
            time.sleep(0.35)

            # 2. Press Enter to raise lock screen wallpaper and focus password prompt
            win32api.keybd_event(0x0D, 0, 0, 0)
            win32api.keybd_event(0x0D, 0, 2, 0)
            time.sleep(0.4)

            # 3. Type the password or PIN
            for ch in pwd:
                vk = win32api.VkKeyScan(ch)
                if vk != -1:
                    vk_code = vk & 0xFF
                    shift = bool((vk >> 8) & 1)
                    if shift:
                        win32api.keybd_event(0x10, 0, 0, 0) # VK_SHIFT down
                    win32api.keybd_event(vk_code, 0, 0, 0)
                    time.sleep(0.015)
                    win32api.keybd_event(vk_code, 0, 2, 0)
                    if shift:
                        win32api.keybd_event(0x10, 0, 2, 0) # VK_SHIFT up
                    time.sleep(0.02)

            time.sleep(0.15)
            # 4. Press Enter to submit
            win32api.keybd_event(0x0D, 0, 0, 0)
            win32api.keybd_event(0x0D, 0, 2, 0)

        threading.Thread(target=_type_unlock, daemon=True).start()

    def execute_action(self, act_name, key_str, cmd_str):
        def _dispatch():
            pyautogui.FAILSAFE = False
            if act_name == "Custom Hotkey":
                if key_str:
                    keys = [k.strip().lower() for k in key_str.replace("+", " ").split()]
                    try:
                        pyautogui.hotkey(*keys)
                        self.sig_log.emit(f"[Trigger] Hotkey: {key_str}")
                    except Exception as e:
                        self.sig_log.emit(f"[Error] Hotkey failed: {e}")
            elif act_name == "Launch Program / Command":
                if cmd_str:
                    try:
                        subprocess.Popen(cmd_str, shell=True)
                        self.sig_log.emit(f"[Trigger] Launched: {cmd_str}")
                    except Exception as e:
                        self.sig_log.emit(f"[Error] Command failed: {e}")
            elif act_name == "Media Play / Pause":
                pyautogui.press("playpause")
                self.sig_log.emit("[Trigger] Media Play/Pause")
            elif act_name == "Media Next Track":
                pyautogui.press("nexttrack")
                self.sig_log.emit("[Trigger] Media Next Track")
            elif act_name == "Media Previous Track":
                pyautogui.press("prevtrack")
                self.sig_log.emit("[Trigger] Media Prev Track")
            elif act_name == "Mute / Unmute Volume":
                if self.volume_ctrl:
                    try:
                        is_muted = self.volume_ctrl.GetMute()
                        self.volume_ctrl.SetMute(not is_muted, None)
                        self.send_device_command("BEEP_MUTE_ON" if not is_muted else "BEEP_MUTE_OFF")
                        self.sig_log.emit(f"[Trigger] Mute Toggled: {not is_muted}")
                    except Exception:
                        pyautogui.press("volumemute")
                else:
                    pyautogui.press("volumemute")
            elif act_name == "Task View (Win+Tab)":
                pyautogui.hotkey("win", "tab")
            elif act_name == "Switch Windows (Alt+Tab)":
                pyautogui.hotkey("alt", "tab")
            elif act_name == "Show Desktop (Win+D)":
                pyautogui.hotkey("win", "d")
            elif act_name == "Open Task Manager (Ctrl+Shift+Esc)":
                pyautogui.hotkey("ctrl", "shift", "esc")
            elif act_name == "Lock Workstation (Win+L)":
                subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
            elif act_name == "Unlock Laptop (Type Password)":
                self.execute_unlock()
                self.sig_log.emit("[Trigger] Quick Unlock triggered via DekDo")

        threading.Thread(target=_dispatch, daemon=True).start()

    def send_device_command(self, cmd_str):
        if self.serial_inst and self.serial_inst.is_open:
            try:
                self.serial_inst.write((cmd_str + "\n").encode("utf-8"))
            except Exception:
                pass

    def apply_directional(self, direction_cfg, intensity, dt, is_horizontal):
        act = direction_cfg.get("action", "None")
        speed = float(self.config.get("speed", 40.0))
        now = time.monotonic()

        # Rate-limit discrete volume steps according to push intensity
        step_interval = max(0.04, 0.28 - (intensity * 0.20))

        if act == "Volume Up":
            step_vol = max(0.5, intensity * 2.0)
            if now - self.last_volume_time >= 0.04:
                self.last_volume_time = now
                if self.volume_ctrl:
                    try:
                        cur = self.volume_ctrl.GetMasterVolumeLevelScalar()
                        new_val = min(1.0, cur + (step_vol / 100.0))
                        self.volume_ctrl.SetMasterVolumeLevelScalar(new_val, None)
                        self.cur_volume = new_val * 100.0
                        self.sig_vol_change.emit(self.cur_volume)
                    except Exception:
                        win32api.keybd_event(0xAF, 0, 1, 0)
                        win32api.keybd_event(0xAF, 0, 1 | 2, 0)
                else:
                    win32api.keybd_event(0xAF, 0, 1, 0)
                    win32api.keybd_event(0xAF, 0, 1 | 2, 0)
        elif act == "Volume Down":
            step_vol = max(0.5, intensity * 2.0)
            if now - self.last_volume_time >= 0.04:
                self.last_volume_time = now
                if self.volume_ctrl:
                    try:
                        cur = self.volume_ctrl.GetMasterVolumeLevelScalar()
                        new_val = max(0.0, cur - (step_vol / 100.0))
                        self.volume_ctrl.SetMasterVolumeLevelScalar(new_val, None)
                        self.cur_volume = new_val * 100.0
                        self.sig_vol_change.emit(self.cur_volume)
                    except Exception:
                        win32api.keybd_event(0xAE, 0, 1, 0)
                        win32api.keybd_event(0xAE, 0, 1 | 2, 0)
                else:
                    win32api.keybd_event(0xAE, 0, 1, 0)
                    win32api.keybd_event(0xAE, 0, 1 | 2, 0)
        elif act == "Brightness Up":
            self.cur_brightness = min(100.0, self.cur_brightness + (intensity * speed * dt))
            if now - self.last_brightness_time >= 0.08:
                self.last_brightness_time = now
                with self._brightness_lock:
                    self._brightness_queue.append(self.cur_brightness)
                self.sig_bright_change.emit(int(self.cur_brightness))
        elif act == "Brightness Down":
            self.cur_brightness = max(0.0, self.cur_brightness - (intensity * speed * dt))
            if now - self.last_brightness_time >= 0.08:
                self.last_brightness_time = now
                with self._brightness_lock:
                    self._brightness_queue.append(self.cur_brightness)
                self.sig_bright_change.emit(int(self.cur_brightness))
        elif act == "Scroll Up (Mouse Wheel)":
            pyautogui.scroll(int(intensity * 120 * dt * 5))
        elif act == "Scroll Down (Mouse Wheel)":
            pyautogui.scroll(-int(intensity * 120 * dt * 5))
        elif act == "Mouse Cursor Move":
            move_px = int(intensity * speed * dt * 25)
            if is_horizontal:
                pyautogui.moveRel(move_px, 0)
            else:
                pyautogui.moveRel(0, -move_px)

    def find_target_port(self):
        target = self.config.get("port", "Auto-Detect")
        if target and target != "Auto-Detect":
            return target

        all_ports = list_ports.comports()
        # Strictly look for USB Cable UART bridges (CP210x, Silicon Labs, CH340, FTDI, or COM9)
        for p in all_ports:
            desc = (p.description or "").upper()
            hwid = (p.hwid or "").upper()
            if "BLUETOOTH" in desc or "BTHENUM" in hwid:
                continue
            if "CP210" in desc or "10C4:EA60" in hwid or p.device == "COM9" or "SILICON" in desc:
                return p.device

        for p in all_ports:
            desc = (p.description or "").upper()
            hwid = (p.hwid or "").upper()
            if "BLUETOOTH" in desc or "BTHENUM" in hwid:
                continue
            if "USB" in desc or "USB" in hwid:
                return p.device

        # If cable is not plugged in, return None
        return None

    def run(self):
        self._running = True
        baud = int(self.config.get("baud_rate", 115200))

        self.init_audio()
        self.init_brightness()

        # Pure USB Cable Mode:
        # Plugged in -> Instantly Connected.
        # Unplugged -> Disconnected.
        while self._running:
            target_port = self.find_target_port()

            if not target_port:
                self.sig_connected.emit(False, "Plug in USB Cable to connect DekDo")
                # Wait briefly and re-scan for USB cable plug-in
                for _ in range(5):
                    if not self._running:
                        return
                    time.sleep(0.1)
                continue

            self.sig_log.emit(f"Connecting to DekDo on USB {target_port}...")

            try:
                # Open USB serial port cleanly without toggling RTS/DTR to avoid rebooting the ESP32
                s = serial.Serial()
                s.port = target_port
                s.baudrate = baud
                s.timeout = 0.25
                s.dtr = False
                s.rts = False
                s.open()
                self.serial_inst = s
                self.sig_connected.emit(True, f"Connected (USB Cable: {target_port})")
                self.sig_log.emit(f"✓ DekDo USB Cable Connected on {target_port}!")
                time.sleep(0.05)
                self.serial_inst.reset_input_buffer()
            except Exception as e:
                self.sig_connected.emit(False, "Plug in USB Cable to connect DekDo")
                if self.serial_inst:
                    try:
                        self.serial_inst.close()
                    except Exception:
                        pass
                    self.serial_inst = None
                
                for _ in range(5):
                    if not self._running:
                        return
                    time.sleep(0.1)
                continue

            last_time = time.monotonic()
            last_ui_emit = 0
            btn_debounce = {}
            sw_click_count = 0
            sw_last_click_time = 0

            # Active USB link: runs while the cable is plugged in
            while self._running and self.serial_inst and self.serial_inst.is_open:
                try:
                    raw_bytes = self.serial_inst.readline()
                    line = raw_bytes.decode("utf-8", errors="ignore").strip()
                except (serial.SerialException, OSError, Exception) as e:
                    # Physical cable unplugged
                    self.sig_log.emit("USB Cable disconnected.")
                    break

                now = time.monotonic()
                dt = min(now - last_time, 0.1)
                last_time = now

                # Check pending single-click timeout for joystick button
                unlock_btn_setting = str(self.config.get("unlock_button", "joystick_double"))
                is_double_unlock = unlock_btn_setting in ("joystick_double", "double_click", "sw_double")

                if sw_click_count == 1:
                    if is_double_unlock:
                        # Wait 300ms to see if a second click arrives
                        if now - sw_last_click_time > 0.30:
                            sw_click_count = 0
                            # Confirmed single click: Mute / toggle volume
                            sw_cfg = self.config.get("joystick_click", {})
                            self.execute_action(sw_cfg.get("action", "None"), sw_cfg.get("key", ""), sw_cfg.get("cmd", ""))
                    else:
                        sw_click_count = 0
                        sw_cfg = self.config.get("joystick_click", {})
                        self.execute_action(sw_cfg.get("action", "None"), sw_cfg.get("key", ""), sw_cfg.get("cmd", ""))

                if not line:
                    continue

                parts = line.split(",")

                # Joystick Packet: J,x,y
                if parts[0] == "J" and len(parts) == 3:
                    try:
                        x = int(parts[1])
                        y = int(parts[2])
                    except ValueError:
                        continue

                    if self.calibrating:
                        self.calib_samples_x.append(x)
                        self.calib_samples_y.append(y)
                        if now - self.calib_start >= 1.5:
                            if len(self.calib_samples_x) >= 8:
                                self.center_x = sum(self.calib_samples_x) / len(self.calib_samples_x)
                                self.center_y = sum(self.calib_samples_y) / len(self.calib_samples_y)
                                self.sig_calibrated.emit(self.center_x, self.center_y)
                                self.sig_log.emit(f"✓ Calibration finished! Center: X={int(self.center_x)}, Y={int(self.center_y)}")
                            self.calibrating = False

                    inv_x = self.config.get("invert_x", False)
                    inv_y = not self.config.get("invert_y", False)

                    norm_x = self.normalize(x, self.center_x, invert=inv_x)
                    norm_y = self.normalize(y, self.center_y, invert=inv_y)

                    # Throttle UI signal to 30 FPS to avoid Qt Event Queue congestion
                    if now - last_ui_emit >= 0.033:
                        last_ui_emit = now
                        self.sig_joystick.emit(x, y, norm_x, norm_y)

                    if not self.calibrating:
                        if norm_x < 0:
                            self.apply_directional(self.config.get("joystick_left", {}), abs(norm_x), dt, is_horizontal=True)
                        elif norm_x > 0:
                            self.apply_directional(self.config.get("joystick_right", {}), abs(norm_x), dt, is_horizontal=True)

                        if norm_y > 0:
                            self.apply_directional(self.config.get("joystick_up", {}), abs(norm_y), dt, is_horizontal=False)
                        elif norm_y < 0:
                            self.apply_directional(self.config.get("joystick_down", {}), abs(norm_y), dt, is_horizontal=False)

                # Button Packet: B,id,state
                elif parts[0] == "B" and len(parts) == 3:
                    try:
                        b_id = int(parts[1])
                        st = int(parts[2])
                    except ValueError:
                        continue

                    self.sig_button.emit(b_id, st)

                    if st == 1:
                        if now - btn_debounce.get(b_id, 0) > 0.08:
                            btn_debounce[b_id] = now
                            unlock_btn_setting = str(self.config.get("unlock_button", ""))
                            if unlock_btn_setting == str(b_id):
                                self.sig_log.emit(f"[Trigger] Button {b_id} assigned to Unlock Laptop!")
                                self.execute_unlock()
                            else:
                                b_cfg = self.config.get("buttons", {}).get(str(b_id), {})
                                self.execute_action(b_cfg.get("action", "None"), b_cfg.get("key", ""), b_cfg.get("cmd", ""))

                # Switch Packet: S,state
                elif parts[0] == "S" and len(parts) == 2:
                    try:
                        sw_st = int(parts[1])
                    except ValueError:
                        continue

                    self.sig_switch.emit(sw_st)
                    if sw_st == 1:
                        if now - btn_debounce.get("sw", 0) > 0.08:
                            btn_debounce["sw"] = now
                            
                            unlock_btn_setting = str(self.config.get("unlock_button", "joystick_double"))
                            if unlock_btn_setting in ("joystick_double", "double_click", "sw_double"):
                                if sw_click_count == 0:
                                    sw_click_count = 1
                                    sw_last_click_time = now
                                elif sw_click_count == 1:
                                    if now - sw_last_click_time <= 0.35:
                                        # Confirmed double click!
                                        sw_click_count = 0
                                        self.sig_log.emit("[Trigger] Joystick Double-Click detected -> Unlocking laptop!")
                                        self.execute_unlock()
                                    else:
                                        sw_click_count = 1
                                        sw_last_click_time = now
                            else:
                                sw_cfg = self.config.get("joystick_click", {})
                                self.execute_action(sw_cfg.get("action", "None"), sw_cfg.get("key", ""), sw_cfg.get("cmd", ""))

            # Cleanup broken port before looping back to reconnect
            if self.serial_inst:
                try:
                    self.serial_inst.close()
                except Exception:
                    pass
                self.serial_inst = None
            self.sig_connected.emit(False, "Searching for DekDo...")
            time.sleep(1.0)


# ==============================================================
# RADIAL JOYSTICK VISUALIZER
# ==============================================================
class JoystickVisualizer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.norm_x = 0.0
        self.norm_y = 0.0
        self.is_pressed = False
        self.deadzone = 0.15

    def set_deadzone(self, dz):
        self.deadzone = dz
        self.update()

    def update_position(self, norm_x, norm_y):
        self.norm_x = norm_x
        self.norm_y = norm_y
        self.update()

    def set_switch(self, pressed):
        self.is_pressed = bool(pressed)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        center = QPointF(w / 2, h / 2)
        radius = (w / 2) - 12

        # Base circle
        painter.setBrush(QBrush(QColor("#181822")))
        painter.setPen(QPen(QColor("#353548"), 2))
        painter.drawEllipse(center, radius, radius)

        # Crosshairs
        painter.setPen(QPen(QColor("#2d2d3e"), 1, Qt.DashLine))
        painter.drawLine(center.x() - radius, center.y(), center.x() + radius, center.y())
        painter.drawLine(center.x(), center.y() - radius, center.x(), center.y() + radius)

        # Deadzone circle
        dz_r = radius * self.deadzone
        painter.setBrush(QBrush(QColor(0, 188, 212, 22)))
        painter.setPen(QPen(QColor("#00bcd4"), 1, Qt.DotLine))
        painter.drawEllipse(center, dz_r, dz_r)

        clamped_x = max(-1.0, min(1.0, self.norm_x))
        clamped_y = max(-1.0, min(1.0, self.norm_y))

        stick_x = center.x() + (clamped_x * (radius - 22))
        stick_y = center.y() - (clamped_y * (radius - 22))

        painter.setPen(QPen(QColor("#00bcd4"), 2))
        painter.drawLine(center, QPointF(stick_x, stick_y))

        stick_radius = 16 if not self.is_pressed else 14
        grad = QLinearGradient(stick_x - stick_radius, stick_y - stick_radius, stick_x + stick_radius, stick_y + stick_radius)
        if self.is_pressed:
            grad.setColorAt(0, QColor("#e74c3c"))
            grad.setColorAt(1, QColor("#c0392b"))
        else:
            grad.setColorAt(0, QColor("#00e5ff"))
            grad.setColorAt(1, QColor("#00838f"))

        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(QColor("#ffffff"), 1.8))
        painter.drawEllipse(QPointF(stick_x, stick_y), stick_radius, stick_radius)


# ==============================================================
# AUTHENTIC WINDOWS 11 FLUENT FLYOUT (VOLUME & BRIGHTNESS)
# ==============================================================
class Windows11OSDFlyout(QWidget):
    def __init__(self, mode="volume"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(304, 52)
        self.mode = mode

        accent = self._get_accent_hex()
        is_dark = self._is_dark_mode()
        self.bg_color = QColor(32, 32, 32, 235) if is_dark else QColor(243, 243, 243, 235)
        self.border_color = QColor(255, 255, 255, 28) if is_dark else QColor(0, 0, 0, 18)
        self.text_color = "#ffffff" if is_dark else "#1f1f1f"
        self.track_color = "rgba(255, 255, 255, 0.22)" if is_dark else "rgba(0, 0, 0, 0.15)"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 18, 0)
        layout.setSpacing(12)

        # Segoe Fluent Icons: U+E767 = Volume, U+E74F = Mute, U+E706 = Brightness
        self.lbl_icon = QLabel("\ue767" if mode == "volume" else "\ue706")
        self.lbl_icon.setFont(QFont("Segoe Fluent Icons", 14))
        self.lbl_icon.setStyleSheet(f"color: {self.text_color};")
        layout.addWidget(self.lbl_icon)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(50)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {self.track_color};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {accent};
                border-radius: 2px;
            }}
        """)
        layout.addWidget(self.bar, stretch=1)

        self.lbl_val = QLabel("50")
        self.lbl_val.setFont(QFont("Segoe UI Variable Text", 10, QFont.Medium))
        self.lbl_val.setStyleSheet(f"color: {self.text_color}; min-width: 24px;")
        self.lbl_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.lbl_val)

        # Smooth elevation drop shadow matching Windows 11 shell
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 110))
        shadow.setOffset(0, 6)
        self.setGraphicsEffect(shadow)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._fade_out)

        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(160)

    def _get_accent_hex(self):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM")
            val, _ = winreg.QueryValueEx(k, "AccentColor")
            r, g, b = val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF
            if r > 50 or g > 50 or b > 50:
                return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            pass
        return "#60cdff"

    def _is_dark_mode(self):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            val, _ = winreg.QueryValueEx(k, "SystemUsesLightTheme")
            return val == 0
        except Exception:
            return True

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QBrush(self.bg_color))
        painter.setPen(QPen(self.border_color, 1))
        painter.drawRoundedRect(rect, 25, 25)

    def trigger_value(self, val, is_muted=False):
        val = max(0, min(100, int(val)))
        self.bar.setValue(val)
        self.lbl_val.setText(str(val))

        if self.mode == "volume":
            if is_muted or val == 0:
                self.lbl_icon.setText("\ue74f") # Muted speaker icon
            elif val < 33:
                self.lbl_icon.setText("\ue992") # Low volume icon
            elif val < 66:
                self.lbl_icon.setText("\ue993") # Medium volume icon
            else:
                self.lbl_icon.setText("\ue767") # High volume icon
        else:
            self.lbl_icon.setText("\ue706") # Brightness sun icon

        # Position flyout in lower center above taskbar (authentic Windows 11 position)
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = avail.x() + (avail.width() - self.width()) // 2
            y = avail.y() + avail.height() - self.height() - 48
            self.move(x, y)

        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.setEasingCurve(QEasingCurve.OutCubic)
            self.anim.start()
        else:
            self.setWindowOpacity(1.0)

        self.hide_timer.start(1600)

    def _fade_out(self):
        self.anim.stop()
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.InCubic)
        self.anim.finished.connect(self._on_fade_finished)
        self.anim.start()

    def _on_fade_finished(self):
        try:
            self.anim.finished.disconnect(self._on_fade_finished)
        except Exception:
            pass
        self.hide()


# Alias for backward compatibility
Windows11BrightnessFlyout = Windows11OSDFlyout


# ==============================================================
# MAIN MODERN WINDOW
# ==============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DekDo - Hardware Command Suite")
        self.setMinimumSize(1040, 800)
        self.config = load_app_config()
        self.worker = None

        # Windows 11 Fluent Flyout Widgets (Volume & Brightness)
        self.volume_flyout = Windows11OSDFlyout(mode="volume")
        self.brightness_flyout = Windows11OSDFlyout(mode="brightness")

        self.apply_dark_theme()
        self.init_ui()
        self.refresh_ports()
        self.setup_tray()

        # Automatically connect on startup
        QTimer.singleShot(600, self.auto_start_connect)

    def auto_start_connect(self):
        if not self.worker or not self.worker._running:
            self.toggle_connection()

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #121217; }
            QWidget { color: #e0e0e0; font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; }
            QFrame, QGroupBox { background-color: #1a1a24; border: 1px solid #282836; border-radius: 8px; }
            QGroupBox { font-weight: bold; font-size: 13px; padding-top: 14px; margin-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding-left: 10px; color: #00bcd4; }
            QTabWidget::pane { border: 1px solid #282836; background-color: #16161f; border-radius: 8px; }
            QTabBar::tab { background-color: #1a1a24; color: #a0a0b0; padding: 10px 22px; margin-right: 4px; border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: 600; }
            QTabBar::tab:selected { background-color: #242436; color: #ffffff; border-bottom: 2px solid #00bcd4; }
            QPushButton { background-color: #28283c; border: 1px solid #3d3d54; border-radius: 6px; padding: 8px 16px; font-weight: 600; color: #ffffff; }
            QPushButton:hover { background-color: #353550; border-color: #00bcd4; }
            QPushButton:pressed { background-color: #1e1e2d; }
            QPushButton#btnConnect { background-color: #27ae60; border: none; }
            QPushButton#btnConnect:hover { background-color: #2ecc71; }
            QPushButton#btnSave { background-color: #2980b9; border: none; }
            QPushButton#btnSave:hover { background-color: #3498db; }
            QPushButton#btnCalibrate { background-color: #8e44ad; border: none; }
            QPushButton#btnCalibrate:hover { background-color: #9b59b6; }
            QComboBox { background-color: #222230; border: 1px solid #353548; border-radius: 5px; padding: 5px 10px; color: #ffffff; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView { background-color: #1c1c28; border: 1px solid #353548; selection-background-color: #00bcd4; selection-color: #000000; }
            QLineEdit { background-color: #222230; border: 1px solid #353548; border-radius: 5px; padding: 6px 10px; color: #ffffff; }
            QLineEdit:focus { border: 1px solid #00bcd4; }
            QSlider::groove:horizontal { height: 6px; background: #2a2a3c; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #00bcd4; border-radius: 3px; }
            QSlider::handle:horizontal { background: #ffffff; width: 16px; margin-top: -5px; margin-bottom: -5px; border-radius: 8px; }
            QProgressBar { border: 1px solid #353548; border-radius: 4px; background-color: #1a1a24; height: 12px; text-align: center; color: transparent; }
            QProgressBar::chunk { background-color: #00bcd4; border-radius: 3px; }
            QTextEdit { background-color: #0d0d12; border: 1px solid #252535; border-radius: 6px; color: #5af78e; font-family: 'Consolas', monospace; font-size: 11px; }
            QCheckBox { spacing: 8px; }
        """)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # Header Frame
        header_frame = QFrame()
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(16, 12, 16, 12)

        title_box = QVBoxLayout()
        lbl_app_title = QLabel("DekDo")
        lbl_app_title.setStyleSheet("font-size: 22px; font-weight: 800; color: #00bcd4; letter-spacing: 2px;")
        lbl_app_sub = QLabel("Smart Desk Command Suite")
        lbl_app_sub.setStyleSheet("font-size: 11px; color: #7f8fa6;")
        title_box.addWidget(lbl_app_title)
        title_box.addWidget(lbl_app_sub)
        header_layout.addLayout(title_box)

        header_layout.addStretch()

        lbl_port = QLabel("Target Port:")
        lbl_port.setStyleSheet("font-weight: 600; color: #dcdde1;")
        header_layout.addWidget(lbl_port)

        self.combo_ports = QComboBox()
        self.combo_ports.setMinimumWidth(130)
        header_layout.addWidget(self.combo_ports)

        self.btn_refresh = QPushButton("↻ Refresh")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        header_layout.addWidget(self.btn_refresh)

        self.lbl_status_pill = QLabel("● Disconnected")
        self.lbl_status_pill.setStyleSheet("color: #e74c3c; font-weight: bold; padding: 4px 10px; background-color: #2c1a1d; border-radius: 4px;")
        header_layout.addWidget(self.lbl_status_pill)

        self.btn_connect = QPushButton("Connect Device")
        self.btn_connect.setObjectName("btnConnect")
        self.btn_connect.setMinimumWidth(130)
        self.btn_connect.clicked.connect(self.toggle_connection)
        header_layout.addWidget(self.btn_connect)

        main_layout.addWidget(header_frame)

        # Tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs, stretch=1)

        self.tab_joystick = QWidget()
        self.tab_buttons = QWidget()
        self.tab_unlock = QWidget()
        self.tab_telemetry = QWidget()

        self.tabs.addTab(self.tab_joystick, "🕹 Joystick Customization")
        self.tabs.addTab(self.tab_buttons, "🔘 Button Customization (1 - 4)")
        self.tabs.addTab(self.tab_unlock, "🔐 Quick Unlock & Password")
        self.tabs.addTab(self.tab_telemetry, "📊 Live Telemetry & Calibration")

        self.setup_joystick_tab()
        self.setup_buttons_tab()
        self.setup_unlock_tab()
        self.setup_telemetry_tab()

        # Bottom Bar
        bot_frame = QFrame()
        bot_layout = QHBoxLayout(bot_frame)
        bot_layout.setContentsMargins(14, 10, 14, 10)

        self.btn_save = QPushButton("💾 Save & Apply Configuration")
        self.btn_save.setObjectName("btnSave")
        self.btn_save.setMinimumHeight(38)
        self.btn_save.clicked.connect(self.apply_and_save)
        bot_layout.addWidget(self.btn_save)

        self.lbl_feedback = QLabel("Ready")
        self.lbl_feedback.setStyleSheet("color: #718093; margin-left: 10px;")
        bot_layout.addWidget(self.lbl_feedback)

        bot_layout.addStretch()

        self.btn_minimize = QPushButton("Hide to Tray")
        self.btn_minimize.clicked.connect(self.hide)
        bot_layout.addWidget(self.btn_minimize)

        main_layout.addWidget(bot_frame)

    def setup_joystick_tab(self):
        layout = QVBoxLayout(self.tab_joystick)
        layout.setContentsMargins(16, 16, 16, 16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        scroll_layout = QVBoxLayout(container)
        scroll_layout.setSpacing(14)

        # Horizontal
        grp_x = QGroupBox("Horizontal Axis (Left / Right Movement)")
        grid_x = QGridLayout(grp_x)
        grid_x.addWidget(QLabel("Tilt Joystick LEFT:"), 0, 0)
        self.combo_x_left = QComboBox()
        self.combo_x_left.addItems(JOYSTICK_ACTIONS)
        self.combo_x_left.setCurrentText(self.config.get("joystick_left", {}).get("action", "Volume Down"))
        grid_x.addWidget(self.combo_x_left, 0, 1)

        self.txt_x_left_key = QLineEdit()
        self.txt_x_left_key.setPlaceholderText("Shortcut (e.g. alt+left)")
        self.txt_x_left_key.setText(self.config.get("joystick_left", {}).get("key", ""))
        grid_x.addWidget(self.txt_x_left_key, 0, 2)

        grid_x.addWidget(QLabel("Tilt Joystick RIGHT:"), 1, 0)
        self.combo_x_right = QComboBox()
        self.combo_x_right.addItems(JOYSTICK_ACTIONS)
        self.combo_x_right.setCurrentText(self.config.get("joystick_right", {}).get("action", "Volume Up"))
        grid_x.addWidget(self.combo_x_right, 1, 1)

        self.txt_x_right_key = QLineEdit()
        self.txt_x_right_key.setPlaceholderText("Shortcut (e.g. alt+right)")
        self.txt_x_right_key.setText(self.config.get("joystick_right", {}).get("key", ""))
        grid_x.addWidget(self.txt_x_right_key, 1, 2)

        self.chk_inv_x = QCheckBox("Invert Horizontal Direction (Left ⟷ Right)")
        self.chk_inv_x.setChecked(self.config.get("invert_x", False))
        grid_x.addWidget(self.chk_inv_x, 2, 0, 1, 3)
        scroll_layout.addWidget(grp_x)

        # Vertical
        grp_y = QGroupBox("Vertical Axis (Up / Down Movement)")
        grid_y = QGridLayout(grp_y)
        grid_y.addWidget(QLabel("Tilt Joystick UP:"), 0, 0)
        self.combo_y_up = QComboBox()
        self.combo_y_up.addItems(JOYSTICK_ACTIONS)
        self.combo_y_up.setCurrentText(self.config.get("joystick_up", {}).get("action", "Brightness Up"))
        grid_y.addWidget(self.combo_y_up, 0, 1)

        self.txt_y_up_key = QLineEdit()
        self.txt_y_up_key.setPlaceholderText("Shortcut (e.g. win+up)")
        self.txt_y_up_key.setText(self.config.get("joystick_up", {}).get("key", ""))
        grid_y.addWidget(self.txt_y_up_key, 0, 2)

        grid_y.addWidget(QLabel("Tilt Joystick DOWN:"), 1, 0)
        self.combo_y_down = QComboBox()
        self.combo_y_down.addItems(JOYSTICK_ACTIONS)
        self.combo_y_down.setCurrentText(self.config.get("joystick_down", {}).get("action", "Brightness Down"))
        grid_y.addWidget(self.combo_y_down, 1, 1)

        self.txt_y_down_key = QLineEdit()
        self.txt_y_down_key.setPlaceholderText("Shortcut (e.g. win+down)")
        self.txt_y_down_key.setText(self.config.get("joystick_down", {}).get("key", ""))
        grid_y.addWidget(self.txt_y_down_key, 1, 2)

        self.chk_inv_y = QCheckBox("Invert Vertical Direction (Up ⟷ Down)")
        self.chk_inv_y.setChecked(self.config.get("invert_y", False))
        grid_y.addWidget(self.chk_inv_y, 2, 0, 1, 3)
        scroll_layout.addWidget(grp_y)

        # Center Click
        grp_sw = QGroupBox("Joystick Center Click")
        grid_sw = QGridLayout(grp_sw)
        grid_sw.addWidget(QLabel("Joystick CLICK:"), 0, 0)
        self.combo_sw = QComboBox()
        self.combo_sw.addItems(BUTTON_ACTIONS)
        self.combo_sw.setCurrentText(self.config.get("joystick_click", {}).get("action", "Mute / Unmute Volume"))
        grid_sw.addWidget(self.combo_sw, 0, 1)

        self.txt_sw_key = QLineEdit()
        self.txt_sw_key.setPlaceholderText("Shortcut (e.g. win+d or space)")
        self.txt_sw_key.setText(self.config.get("joystick_click", {}).get("key", ""))
        grid_sw.addWidget(self.txt_sw_key, 0, 2)
        scroll_layout.addWidget(grp_sw)

        # Tuning
        grp_tune = QGroupBox("Sensitivity & Deadzone Tuning")
        grid_tune = QGridLayout(grp_tune)
        grid_tune.addWidget(QLabel("Speed Factor:"), 0, 0)
        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setRange(10, 100)
        self.slider_speed.setValue(int(self.config.get("speed", 40)))
        grid_tune.addWidget(self.slider_speed, 0, 1)
        self.lbl_speed_val = QLabel(f"{self.slider_speed.value()}")
        self.slider_speed.valueChanged.connect(lambda v: self.lbl_speed_val.setText(str(v)))
        grid_tune.addWidget(self.lbl_speed_val, 0, 2)

        grid_tune.addWidget(QLabel("Dead Zone Filter:"), 1, 0)
        self.slider_dz = QSlider(Qt.Horizontal)
        self.slider_dz.setRange(5, 40)
        self.slider_dz.setValue(int(self.config.get("dead_zone", 0.15) * 100))
        grid_tune.addWidget(self.slider_dz, 1, 1)
        self.lbl_dz_val = QLabel(f"{self.slider_dz.value() / 100:.2f}")
        self.slider_dz.valueChanged.connect(self.on_dz_slider_changed)
        grid_tune.addWidget(self.lbl_dz_val, 1, 2)

        scroll_layout.addWidget(grp_tune)
        scroll_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def on_dz_slider_changed(self, v):
        dz = v / 100.0
        self.lbl_dz_val.setText(f"{dz:.2f}")
        if hasattr(self, 'joy_visualizer'):
            self.joy_visualizer.set_deadzone(dz)

    def setup_buttons_tab(self):
        layout = QVBoxLayout(self.tab_buttons)
        layout.setContentsMargins(16, 16, 16, 16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        scroll_layout = QVBoxLayout(container)
        scroll_layout.setSpacing(12)

        self.btn_controls = {}
        pins = {"1": "GPIO 13", "2": "GPIO 14", "3": "GPIO 25", "4": "GPIO 26"}

        for b_id in ["1", "2", "3", "4"]:
            b_cfg = self.config.get("buttons", {}).get(b_id, {})
            grp = QGroupBox(f"Hardware Button {b_id} — {pins.get(b_id)}")
            grid = QGridLayout(grp)

            grid.addWidget(QLabel("Trigger Action:"), 0, 0)
            combo = QComboBox()
            combo.addItems(BUTTON_ACTIONS)
            combo.setCurrentText(b_cfg.get("action", "Custom Hotkey"))
            grid.addWidget(combo, 0, 1)

            grid.addWidget(QLabel("Shortcut / Command:"), 0, 2)
            txt_key = QLineEdit()
            txt_key.setPlaceholderText("e.g. win+e, ctrl+c, or 'calc.exe'")
            txt_key.setText(b_cfg.get("key", "") or b_cfg.get("cmd", ""))
            grid.addWidget(txt_key, 0, 3)

            btn_test = QPushButton("⚡ Test Trigger")
            btn_test.clicked.connect(lambda _, bid=b_id: self.test_button_action(bid))
            grid.addWidget(btn_test, 0, 4)

            scroll_layout.addWidget(grp)
            self.btn_controls[b_id] = {"combo": combo, "key": txt_key}

        scroll_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def setup_unlock_tab(self):
        layout = QVBoxLayout(self.tab_unlock)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        grp_unlock = QGroupBox("🔐 Instant Windows Unlock & Auto-Login")
        grid = QGridLayout(grp_unlock)
        grid.setVerticalSpacing(14)
        grid.setHorizontalSpacing(12)

        info_lbl = QLabel(
            "<b>How it works:</b><br>"
            "Your DekDo connects instantly on boot/wake via direct Bluetooth HID (identical to a wireless mouse/keyboard).<br>"
            "When the assigned unlock button is pressed, DekDo wakes your screen, clears any previous input, "
            "and types your stored Windows password securely into the lock screen within milliseconds."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #a4b0be; font-size: 13px; margin-bottom: 8px;")
        grid.addWidget(info_lbl, 0, 0, 1, 3)

        # Trigger Button Selector
        grid.addWidget(QLabel("Unlock Trigger Trigger:"), 1, 0)
        self.combo_unlock_btn = QComboBox()
        self.combo_unlock_btn.addItems([
            "Joystick Center Button (Double Click)",
            "Button 1 (GPIO 13)",
            "Button 2 (GPIO 14)",
            "Button 3 (GPIO 25)",
            "Button 4 (GPIO 26)",
            "Disabled (Do not trigger)"
        ])
        saved_btn = str(self.config.get("unlock_button", "joystick_double"))
        idx_map = {
            "joystick_double": 0,
            "double_click": 0,
            "sw_double": 0,
            "1": 1,
            "2": 2,
            "3": 3,
            "4": 4,
            "0": 5
        }
        self.combo_unlock_btn.setCurrentIndex(idx_map.get(saved_btn, 0))
        grid.addWidget(self.combo_unlock_btn, 1, 1, 1, 2)

        # Password Input
        grid.addWidget(QLabel("Laptop / Windows Password:"), 2, 0)
        self.txt_unlock_pwd = QLineEdit()
        self.txt_unlock_pwd.setEchoMode(QLineEdit.Password)
        self.txt_unlock_pwd.setPlaceholderText("Enter your Windows user password or PIN")
        self.txt_unlock_pwd.setText(self.config.get("unlock_password", ""))
        grid.addWidget(self.txt_unlock_pwd, 2, 1)

        self.btn_show_pwd = QPushButton("👁 Show")
        self.btn_show_pwd.setCheckable(True)
        self.btn_show_pwd.toggled.connect(self.toggle_pwd_visibility)
        self.btn_show_pwd.setFixedWidth(80)
        grid.addWidget(self.btn_show_pwd, 2, 2)

        # Action Buttons
        btn_box = QHBoxLayout()
        self.btn_flash_pwd = QPushButton("💾 Save Password into DekDo Device")
        self.btn_flash_pwd.setObjectName("btnSave")
        self.btn_flash_pwd.setMinimumHeight(38)
        self.btn_flash_pwd.clicked.connect(self.sync_password_to_device)
        btn_box.addWidget(self.btn_flash_pwd)

        self.btn_clear_device_pwd = QPushButton("🗑 Clear from Device")
        self.btn_clear_device_pwd.setMinimumHeight(38)
        self.btn_clear_device_pwd.setStyleSheet("background-color: #c0392b;")
        self.btn_clear_device_pwd.clicked.connect(self.clear_device_password)
        btn_box.addWidget(self.btn_clear_device_pwd)

        self.btn_test_unlock = QPushButton("⚡ Test Unlock Now")
        self.btn_test_unlock.setMinimumHeight(38)
        self.btn_test_unlock.clicked.connect(self.test_quick_unlock)
        btn_box.addWidget(self.btn_test_unlock)

        grid.addLayout(btn_box, 3, 0, 1, 3)

        self.lbl_unlock_status = QLabel("Device Status: Ready")
        self.lbl_unlock_status.setStyleSheet("color: #2ecc71; font-weight: bold; margin-top: 6px;")
        grid.addWidget(self.lbl_unlock_status, 4, 0, 1, 3)

        layout.addWidget(grp_unlock)
        layout.addStretch()

    def toggle_pwd_visibility(self, checked):
        if checked:
            self.txt_unlock_pwd.setEchoMode(QLineEdit.Normal)
            self.btn_show_pwd.setText("🔒 Hide")
        else:
            self.txt_unlock_pwd.setEchoMode(QLineEdit.Password)
            self.btn_show_pwd.setText("👁 Show")

    def sync_password_to_device(self):
        pwd = self.txt_unlock_pwd.text()
        idx = self.combo_unlock_btn.currentIndex()
        if idx == 0:
            btn_setting = "joystick_double"
        elif idx == 5:
            btn_setting = "0"
        else:
            btn_setting = str(idx)

        self.config["unlock_password"] = pwd
        self.config["unlock_button"] = btn_setting
        save_app_config(self.config)

        # Send command over serial if connected
        if self.worker:
            self.worker.config = self.config
            self.worker.send_device_command(f"SET_PWD:{pwd}")
            self.worker.send_device_command(f"SET_UNLOCK_BTN:{btn_setting}")
            self.lbl_unlock_status.setText("✓ Password and unlock trigger synced to DekDo successfully!")
            self.lbl_feedback.setText("✓ DekDo settings updated!")
        else:
            self.lbl_unlock_status.setText("Saved to config. Connect device to sync.")

    def clear_device_password(self):
        self.txt_unlock_pwd.clear()
        self.config["unlock_password"] = ""
        save_app_config(self.config)
        if self.worker:
            self.worker.send_device_command("CLEAR_PWD")
            self.lbl_unlock_status.setText("✓ Stored password erased from device.")
        else:
            self.lbl_unlock_status.setText("Password cleared from settings.")

    def test_quick_unlock(self):
        self.lbl_unlock_status.setText("⚡ Initiating unlock sequence in 1 second...")
        if self.worker:
            self.worker.execute_unlock()
        else:
            self.lbl_unlock_status.setText("DekDo worker not active.")

    def setup_telemetry_tab(self):
        layout = QHBoxLayout(self.tab_telemetry)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        left_box = QVBoxLayout()
        left_box.setSpacing(12)

        grp_vis = QGroupBox("Real-time Joystick Position")
        vis_layout = QVBoxLayout(grp_vis)
        vis_layout.setAlignment(Qt.AlignCenter)
        self.joy_visualizer = JoystickVisualizer()
        self.joy_visualizer.set_deadzone(float(self.config.get("dead_zone", 0.15)))
        vis_layout.addWidget(self.joy_visualizer)

        self.lbl_telemetry_coords = QLabel("Raw: X=---- | Y=----   Rate: X=0.00 | Y=0.00")
        self.lbl_telemetry_coords.setAlignment(Qt.AlignCenter)
        self.lbl_telemetry_coords.setStyleSheet("font-family: 'Consolas'; font-size: 12px; color: #00bcd4;")
        vis_layout.addWidget(self.lbl_telemetry_coords)

        calib_bar = QHBoxLayout()
        self.btn_calib = QPushButton("🎯 Calibrate Center Now")
        self.btn_calib.setObjectName("btnCalibrate")
        self.btn_calib.clicked.connect(self.start_manual_calibration)
        calib_bar.addWidget(self.btn_calib)

        self.lbl_center_vals = QLabel(f"Center: X={int(self.config.get('calibrated_center_x', 1795))} Y={int(self.config.get('calibrated_center_y', 1805))}")
        self.lbl_center_vals.setStyleSheet("color: #a0a0b0; font-family: 'Consolas';")
        calib_bar.addWidget(self.lbl_center_vals)
        vis_layout.addLayout(calib_bar)
        left_box.addWidget(grp_vis)

        # Gauges
        grp_gauges = QGroupBox("System Sliders")
        gauges_layout = QVBoxLayout(grp_gauges)
        gauges_layout.addWidget(QLabel("Master Volume:"))
        self.bar_volume = QProgressBar()
        self.bar_volume.setRange(0, 100)
        self.bar_volume.setValue(50)
        gauges_layout.addWidget(self.bar_volume)

        gauges_layout.addWidget(QLabel("Screen Brightness:"))
        self.bar_brightness = QProgressBar()
        self.bar_brightness.setRange(0, 100)
        self.bar_brightness.setValue(50)
        gauges_layout.addWidget(self.bar_brightness)
        left_box.addWidget(grp_gauges)

        layout.addLayout(left_box, stretch=1)

        # Logs
        grp_log = QGroupBox("Serial Communication & Event Monitor")
        log_layout = QVBoxLayout(grp_log)
        self.txt_console = QTextEdit()
        self.txt_console.setReadOnly(True)
        log_layout.addWidget(self.txt_console)

        btn_clear = QPushButton("Clear Log")
        btn_clear.clicked.connect(self.txt_console.clear)
        log_layout.addWidget(btn_clear)
        layout.addWidget(grp_log, stretch=2)

    def start_manual_calibration(self):
        if self.worker and self.worker._running:
            self.worker.request_calibration()
            self.lbl_feedback.setText("Calibrating joystick center... please leave stick centered!")
        else:
            self.append_log("Connect device first to calibrate.")

    @Slot(float, float)
    def on_calibrated(self, cx, cy):
        self.config["calibrated_center_x"] = cx
        self.config["calibrated_center_y"] = cy
        self.lbl_center_vals.setText(f"Center: X={int(cx)} Y={int(cy)}")
        save_app_config(self.config)

    def setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("DekDo - Running in Background")
        self.tray.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        self.tray.activated.connect(self.on_tray_activated)

        menu = QMenu()
        act_show = menu.addAction("Open DekDo Settings")
        act_show.triggered.connect(self.showNormal)
        menu.addSeparator()
        act_quit = menu.addAction("Exit DekDo Completely")
        act_quit.triggered.connect(self.quit_app_completely)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger or reason == QSystemTrayIcon.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.activateWindow()

    def quit_app_completely(self):
        if self.worker and self.worker._running:
            self.worker.stop()
            self.worker.wait(1000)
        QApplication.instance().quit()

    def refresh_ports(self):
        self.combo_ports.clear()
        found = ["Auto-Detect"]
        for p in list_ports.comports():
            found.append(p.device)
        self.combo_ports.addItems(found)
        saved = self.config.get("port", "Auto-Detect")
        if saved in found:
            self.combo_ports.setCurrentText(saved)
        else:
            self.combo_ports.setCurrentIndex(0)

    def toggle_connection(self):
        # Disable button briefly to prevent rapid re-click crashes
        self.btn_connect.setEnabled(False)
        self.lbl_feedback.setText("Processing connection change...")

        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1500)
            self.worker = None
            self.update_connection_ui(False, "Disconnected")
            QTimer.singleShot(600, lambda: self.btn_connect.setEnabled(True))
        else:
            self.apply_and_save()
            self.worker = SerialWorker(self.config)
            self.worker.sig_connected.connect(self.update_connection_ui)
            self.worker.sig_log.connect(self.append_log)
            self.worker.sig_joystick.connect(self.on_joystick_data)
            self.worker.sig_button.connect(self.on_button_event)
            self.worker.sig_switch.connect(self.on_switch_event)
            self.worker.sig_vol_change.connect(self.on_volume_update)
            self.worker.sig_bright_change.connect(self.on_brightness_update)
            self.worker.sig_calibrated.connect(self.on_calibrated)
            self.worker.start()
            QTimer.singleShot(800, lambda: self.btn_connect.setEnabled(True))

    @Slot(bool, str)
    def update_connection_ui(self, connected, msg):
        if connected:
            self.lbl_status_pill.setText("● Connected")
            self.lbl_status_pill.setStyleSheet("color: #2ecc71; font-weight: bold; padding: 4px 10px; background-color: #172b1e; border-radius: 4px;")
            self.btn_connect.setText("Disconnect Device")
            self.btn_connect.setStyleSheet("background-color: #c0392b;")
        else:
            self.lbl_status_pill.setText("● Disconnected")
            self.lbl_status_pill.setStyleSheet("color: #e74c3c; font-weight: bold; padding: 4px 10px; background-color: #2c1a1d; border-radius: 4px;")
            self.btn_connect.setText("Connect Device")
            self.btn_connect.setStyleSheet("background-color: #27ae60;")

    @Slot(str)
    def append_log(self, text):
        t_str = time.strftime("%H:%M:%S")
        self.txt_console.append(f"[{t_str}] {text}")
        if self.txt_console.document().blockCount() > 200:
            cursor = self.txt_console.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.select(cursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    @Slot(int, int, float, float)
    def on_joystick_data(self, rx, ry, nx, ny):
        self.joy_visualizer.update_position(nx, ny)
        self.lbl_telemetry_coords.setText(f"Raw: X={rx:4d} | Y={ry:4d}   Rate: X={nx:+.2f} | Y={ny:+.2f}")

    @Slot(int, int)
    def on_button_event(self, b_id, state):
        st_text = "PRESSED" if state == 1 else "RELEASED"
        self.append_log(f"🔘 Button {b_id} -> {st_text}")

    @Slot(int)
    def on_switch_event(self, state):
        self.joy_visualizer.set_switch(state == 1)
        if state == 1:
            self.append_log("🕹 Joystick Push Switch CLICKED")

    @Slot(float)
    def on_volume_update(self, val):
        self.bar_volume.setValue(int(val))
        if hasattr(self, 'volume_flyout') and self.volume_flyout:
            is_muted = False
            if self.worker and self.worker.volume_ctrl:
                try:
                    is_muted = self.worker.volume_ctrl.GetMute()
                except Exception:
                    pass
            self.volume_flyout.trigger_value(val, is_muted=is_muted)

    @Slot(int)
    def on_brightness_update(self, val):
        self.bar_brightness.setValue(val)
        if hasattr(self, 'brightness_flyout') and self.brightness_flyout:
            self.brightness_flyout.trigger_value(val)

    def test_button_action(self, b_id):
        ctrls = self.btn_controls[b_id]
        act = ctrls["combo"].currentText()
        key_or_cmd = ctrls["key"].text()

        worker = SerialWorker(self.config)
        worker.init_audio()
        worker.execute_action(act, key_or_cmd, key_or_cmd)
        self.lbl_feedback.setText(f"Tested: Button {b_id} ({act})")

    def apply_and_save(self):
        self.config["port"] = self.combo_ports.currentText()
        self.config["speed"] = float(self.slider_speed.value())
        self.config["dead_zone"] = float(self.slider_dz.value()) / 100.0
        self.config["invert_x"] = self.chk_inv_x.isChecked()
        self.config["invert_y"] = self.chk_inv_y.isChecked()

        self.config["joystick_left"] = {"action": self.combo_x_left.currentText(), "key": self.txt_x_left_key.text(), "cmd": self.txt_x_left_key.text()}
        self.config["joystick_right"] = {"action": self.combo_x_right.currentText(), "key": self.txt_x_right_key.text(), "cmd": self.txt_x_right_key.text()}
        self.config["joystick_up"] = {"action": self.combo_y_up.currentText(), "key": self.txt_y_up_key.text(), "cmd": self.txt_y_up_key.text()}
        self.config["joystick_down"] = {"action": self.combo_y_down.currentText(), "key": self.txt_y_down_key.text(), "cmd": self.txt_y_down_key.text()}
        self.config["joystick_click"] = {"action": self.combo_sw.currentText(), "key": self.txt_sw_key.text(), "cmd": self.txt_sw_key.text()}

        for b_id, ctrls in self.btn_controls.items():
            self.config["buttons"][b_id] = {
                "name": f"Button {b_id}",
                "action": ctrls["combo"].currentText(),
                "key": ctrls["key"].text(),
                "cmd": ctrls["key"].text()
            }

        if hasattr(self, 'txt_unlock_pwd'):
            self.config["unlock_password"] = self.txt_unlock_pwd.text()
        if hasattr(self, 'combo_unlock_btn'):
            idx = self.combo_unlock_btn.currentIndex()
            if idx == 0:
                btn_setting = "joystick_double"
            elif idx == 5:
                btn_setting = "0"
            else:
                btn_setting = str(idx)
            self.config["unlock_button"] = btn_setting

        if self.worker:
            self.worker.config = self.config
            self.worker.center_x = float(self.config.get("calibrated_center_x", self.worker.center_x))
            self.worker.center_y = float(self.config.get("calibrated_center_y", self.worker.center_y))
            if self.config.get("unlock_password"):
                self.worker.send_device_command(f"SET_PWD:{self.config['unlock_password']}")
            btn_setting = self.config.get("unlock_button", "joystick_double")
            self.worker.send_device_command(f"SET_UNLOCK_BTN:{btn_setting}")

        save_app_config(self.config)
        self.lbl_feedback.setText(f"✓ Configuration saved at {time.strftime('%H:%M:%S')}")

    def closeEvent(self, event):
        # Instead of shutting down the worker, minimize to system tray!
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "DekDo is Still Running",
            "DekDo is active in the background. Your joystick and buttons will continue working! Click the tray icon to re-open.",
            QSystemTrayIcon.Information,
            3000
        )

if __name__ == "__main__":
    IPC_SERVER_NAME = "DekDo_SingleInstance_IPC_v1"

    # Quick IPC check to see if another instance is already running
    test_socket = QLocalSocket()
    test_socket.connectToServer(IPC_SERVER_NAME)
    if test_socket.waitForConnected(300):
        if "--minimized" not in sys.argv:
            # Tell the existing instance to reveal its window
            test_socket.write(b"SHOW\n")
            test_socket.waitForBytesWritten(300)
        test_socket.disconnectFromServer()
        sys.exit(0)

    app = QApplication(sys.argv)
    # Prevent Qt application from exiting when window is minimized to system tray
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()

    # Start local IPC server so subsequent launches can bring this window to focus
    ipc_server = QLocalServer()
    # Remove any stale pipe if previous process crashed
    QLocalServer.removeServer(IPC_SERVER_NAME)
    if ipc_server.listen(IPC_SERVER_NAME):
        def handle_new_connection():
            client = ipc_server.nextPendingConnection()
            if client:
                def read_cmd():
                    data = bytes(client.readAll()).decode("utf-8", errors="ignore")
                    if "SHOW" in data:
                        window.showNormal()
                        window.activateWindow()
                        window.raise_()
                client.readyRead.connect(read_cmd)
        ipc_server.newConnection.connect(handle_new_connection)

    # If launched with --minimized (e.g. from Windows Startup), stay in tray silently
    if "--minimized" in sys.argv:
        pass
    else:
        window.show()
        window.activateWindow()
        window.raise_()

    sys.exit(app.exec())
