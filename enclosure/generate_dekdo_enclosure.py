"""
Parametric 3D Enclosure Generator for DekDo Desktop Control Hub
Designed for Blender 4.x / 5.x Python API (bpy)
Creates a two-part 3D printable enclosure:
  1. Bottom Tray: Houses ESP32 DevKit V1, internal wire routing, USB cable port, and screw standoffs.
  2. Top Faceplate: Cutouts for 2-axis joystick dome, 4 tactile buttons, buzzer sound grill, and mounting posts.
"""

import bpy
import math

def clear_existing_collection(col_name="DekDo_Enclosure"):
    """Removes previous generated objects if they exist."""
    col = bpy.data.collections.get(col_name)
    if col:
        for obj in list(col.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(col)

def create_enclosure():
    clear_existing_collection("DekDo_Enclosure")
    
    # Master collection
    col = bpy.data.collections.new("DekDo_Enclosure")
    bpy.context.scene.collection.children.link(col)
    
    # ---------------- PARAMETERS (in mm -> Blender units 1 = 1m or standard mm) ----------------
    # Using mm units (scale 1.0 = 1mm)
    wall = 2.4              # Shell wall thickness
    outer_l = 115.0         # Total length (X)
    outer_w = 75.0          # Total width (Y)
    base_h = 24.0           # Bottom enclosure height (Z)
    lid_h = 8.0             # Top lid height (Z)
    fillet_r = 4.0          # Corner rounding
    
    # --- 1. Bottom Tray Outer Body ---
    bpy.ops.mesh.primitive_cube_add(
        size=1.0,
        location=(outer_l / 2, outer_w / 2, base_h / 2)
    )
    base_outer = bpy.context.active_object
    base_outer.name = "Enclosure_Bottom_Base"
    base_outer.scale = (outer_l, outer_w, base_h)
    bpy.ops.object.transform_apply(scale=True)
    
    # Hollow interior cutout
    bpy.ops.mesh.primitive_cube_add(
        size=1.0,
        location=(outer_l / 2, outer_w / 2, (base_h / 2) + wall)
    )
    base_inner = bpy.context.active_object
    base_inner.name = "Base_Interior_Cutout"
    base_inner.scale = (outer_l - (2 * wall), outer_w - (2 * wall), base_h)
    bpy.ops.object.transform_apply(scale=True)
    
    # Apply Difference Boolean to Base
    bool_mod = base_outer.modifiers.new(name="HollowBase", type='BOOLEAN')
    bool_mod.operation = 'DIFFERENCE'
    bool_mod.object = base_inner
    bpy.context.view_layer.objects.active = base_outer
    bpy.ops.object.modifier_apply(modifier="HollowBase")
    bpy.data.objects.remove(base_inner, do_unlink=True)
    
    # Bevel corners for smooth ergonomic feel
    bevel = base_outer.modifiers.new(name="Bevel", type='BEVEL')
    bevel.width = fillet_r
    bevel.segments = 4
    
    # USB Port Cutout (Back / Left edge for Micro-USB / USB-C)
    # ESP32 is situated near left side: X=0..55, USB port facing X=0
    usb_w = 11.0
    usb_h = 7.0
    bpy.ops.mesh.primitive_cube_add(
        size=1.0,
        location=(0, outer_w / 2, wall + (usb_h / 2) + 2.0)
    )
    usb_cutter = bpy.context.active_object
    usb_cutter.scale = (wall * 4, usb_w, usb_h)
    bpy.ops.object.transform_apply(scale=True)
    
    bool_usb = base_outer.modifiers.new(name="USBCutout", type='BOOLEAN')
    bool_usb.operation = 'DIFFERENCE'
    bool_usb.object = usb_cutter
    bpy.ops.object.modifier_apply(modifier="USBCutout")
    bpy.data.objects.remove(usb_cutter, do_unlink=True)
    
    # --- 2. Top Lid (Faceplate) ---
    lid_z_offset = base_h + 10.0  # Displayed floating 10mm above the base for clear viewing
    bpy.ops.mesh.primitive_cube_add(
        size=1.0,
        location=(outer_l / 2, outer_w / 2, lid_z_offset + (lid_h / 2))
    )
    lid = bpy.context.active_object
    lid.name = "Enclosure_Top_Lid"
    lid.scale = (outer_l, outer_w, lid_h)
    bpy.ops.object.transform_apply(scale=True)
    
    # Bevel on lid
    lid_bevel = lid.modifiers.new(name="LidBevel", type='BEVEL')
    lid_bevel.width = fillet_r
    lid_bevel.segments = 4
    
    # --- Cutout A: Joystick Dome Hole (Left/Center area, 26mm diameter) ---
    joy_x = 35.0
    joy_y = outer_w / 2
    joy_hole_dia = 26.5
    bpy.ops.mesh.primitive_cylinder_add(
        radius=joy_hole_dia / 2,
        depth=lid_h * 3,
        location=(joy_x, joy_y, lid_z_offset + (lid_h / 2))
    )
    joy_cutter = bpy.context.active_object
    joy_cutter.name = "Joystick_Cutter"
    
    bool_joy = lid.modifiers.new(name="JoyHole", type='BOOLEAN')
    bool_joy.operation = 'DIFFERENCE'
    bool_joy.object = joy_cutter
    bpy.context.view_layer.objects.active = lid
    bpy.ops.object.modifier_apply(modifier="JoyHole")
    bpy.data.objects.remove(joy_cutter, do_unlink=True)
    
    # --- Cutout B: 4 Push Buttons (Right area, 2x2 ergonomic matrix) ---
    # Button center locations:
    btn_positions = [
        (80.0, 50.0),  # Button 1 (Top-Left)
        (96.0, 50.0),  # Button 2 (Top-Right)
        (80.0, 25.0),  # Button 3 (Bottom-Left)
        (96.0, 25.0),  # Button 4 (Bottom-Right)
    ]
    btn_hole_dia = 7.5  # 7.5mm round tactile cap cutout
    
    for idx, (bx, by) in enumerate(btn_positions, 1):
        bpy.ops.mesh.primitive_cylinder_add(
            radius=btn_hole_dia / 2,
            depth=lid_h * 3,
            location=(bx, by, lid_z_offset + (lid_h / 2))
        )
        btn_cutter = bpy.context.active_object
        mod = lid.modifiers.new(name=f"BtnHole_{idx}", type='BOOLEAN')
        mod.operation = 'DIFFERENCE'
        mod.object = btn_cutter
        bpy.context.view_layer.objects.active = lid
        bpy.ops.object.modifier_apply(modifier=f"BtnHole_{idx}")
        bpy.data.objects.remove(btn_cutter, do_unlink=True)
        
    # --- Cutout C: Buzzer Grille (Slots near center) ---
    buzzer_center_x = 60.0
    buzzer_center_y = 25.0
    for slot_idx in range(-2, 3):
        bpy.ops.mesh.primitive_cube_add(
            size=1.0,
            location=(buzzer_center_x + (slot_idx * 2.5), buzzer_center_y, lid_z_offset + (lid_h / 2))
        )
        slot_cutter = bpy.context.active_object
        slot_cutter.scale = (1.2, 10.0, lid_h * 3)
        bpy.ops.object.transform_apply(scale=True)
        
        mod = lid.modifiers.new(name=f"BuzzerSlot_{slot_idx}", type='BOOLEAN')
        mod.operation = 'DIFFERENCE'
        mod.object = slot_cutter
        bpy.context.view_layer.objects.active = lid
        bpy.ops.object.modifier_apply(modifier=f"BuzzerSlot_{slot_idx}")
        bpy.data.objects.remove(slot_cutter, do_unlink=True)
        
    # Link objects to collection
    col.objects.link(base_outer)
    col.objects.link(lid)
    if base_outer.name in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.unlink(base_outer)
    if lid.name in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.unlink(lid)
        
    print("DekDo Enclosure successfully generated in Blender!")

if __name__ == "__main__":
    create_enclosure()
