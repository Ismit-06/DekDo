# DekDo Hardware Hub — 3D Enclosure

This folder contains all 3D CAD models, procedural generation scripts, Blender project files, and 3D printing exports for the **DekDo USB Desktop Control Hub**.

---

## 📁 Folder Contents

| File | Type | Description |
| :--- | :--- | :--- |
| **`dekdo_enclosure.blend`** | Blender Project | Master Blender 5.x scene containing both the base tray and faceplate with boolean modifiers and collections. |
| **`generate_dekdo_enclosure.py`** | Python (bpy) | Parametric procedural generator for Blender. Run inside Blender's scripting tab or via MCP to rebuild geometry with custom dimensions. |
| **`dekdo_bottom_base.stl`** | 3D Mesh (STL) | Printable bottom enclosure tray housing the ESP32 DevKit V1 and wiring cavity with rear USB cable cutout. |
| **`dekdo_top_lid.stl`** | 3D Mesh (STL) | Printable top faceplate featuring apertures for the analog joystick, 4 tactile buttons, and buzzer grille. |
| **`dekdo_assembly.stl`** | 3D Mesh (STL) | Full preview assembly of the top and bottom shells positioned together. |

---

## 📐 Enclosure Dimensions & Hardware Clearances

* **Overall Dimensions**: 115 mm (L) × 75 mm (W) × 32 mm (H assembled)
* **Wall Thickness**: 2.4 mm (rigid structure for desktop use)
* **Corner Fillet**: 4.0 mm ergonomic bevel
* **Base Depth**: 24 mm internal height for ESP32 DevKit V1 + jumper wires
* **Top Lid Thickness**: 8 mm faceplate with beveled edges

### Cutouts & Component Locations:
1. **Analog 2-Axis Thumbstick (KY-023 / PS2 Module)**:
   * Aperture: 26.5 mm circular dome opening
   * Position: Left side ($X = 35.0\text{ mm}$, $Y = 37.5\text{ mm}$)
2. **4x Tactile Push Buttons**:
   * Apertures: 7.5 mm diameter circular holes (sized for standard round switch caps)
   * Arrangement: 2×2 ergonomic thumb matrix ($X = [80\text{ mm}, 96\text{ mm}]$, $Y = [25\text{ mm}, 50\text{ mm}]$)
3. **Piezo / Active Buzzer**:
   * Grille: 5 slotted sound vents (1.2 mm × 10 mm) near $X = 60\text{ mm}$
4. **USB Port Cutout**:
   * Size: 11.0 mm × 7.0 mm rectangular slot on the rear left wall for Micro-USB / USB-C data cable clearance

---

## 🖨️ Recommended 3D Printing Settings

* **Filament**: PLA, PLA+, or PETG (PETG recommended for snap-fit flexibility)
* **Layer Height**: 0.20 mm (or 0.16 mm for smoother top face bevels)
* **Infill**: 20% – 25% (Gyroid or Grid for structural rigidity)
* **Perimeters / Wall Loops**: 3 to 4 walls
* **Supports**:
  * **Bottom Base**: Print flat on build plate; support required only under the USB bridge cutout.
  * **Top Lid**: Print upside down (flat top surface on the build plate) to eliminate support needs for the button and joystick holes.
* **Tolerances**: Pre-compensated with +0.3 mm clearance on circular cutouts for FDM printer shrinkage.

---

## 🛠️ Modifying the Enclosure in Blender

You can modify the model interactively using the **Antigravity Blender MCP connector** or natively in Blender:

1. Open `dekdo_enclosure.blend` in Blender.
2. Ensure the **MCP for Blender** server is active (<kbd>N</kbd> panel in 3D Viewport).
3. If you want to modify hole sizes or outer dimensions, adjust the parameters at the top of `generate_dekdo_enclosure.py` and run it:
   ```python
   wall = 2.4              # Shell thickness
   outer_l = 115.0         # Total length
   outer_w = 75.0          # Total width
   base_h = 24.0           # Base height
   btn_hole_dia = 7.5      # Button cap diameter
   joy_hole_dia = 26.5     # Joystick opening
   ```
4. Re-export to STL directly from Blender:
   `File > Export > Stl (.stl)` or use `bpy.ops.wm.stl_export()`.
