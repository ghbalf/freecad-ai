# Heat-Set Thread Insert Holes

Create properly sized holes for heat-set threaded inserts (common in 3D printed parts).

## Standard insert sizes (hole diameter / depth)
- **M2**: hole d=3.2mm, depth=3.5mm
- **M2.5**: hole d=3.6mm, depth=4.0mm
- **M3**: hole d=4.0mm, depth=5.0mm
- **M4**: hole d=5.6mm, depth=6.0mm
- **M5**: hole d=6.4mm, depth=7.0mm

## Parameters to ask (if not provided)
- **Insert size**: M2 / M2.5 / M3 / M4 / M5
- **Target object**: which body/feature to cut into
- **Positions**: XY coordinates or face + offsets
- **Through-hole below insert?**: if yes, add a smaller through-hole (screw clearance) below the insert pocket

## Construction
1. For each position, create a sketch on the target face
2. Draw a circle with the insert hole diameter
3. Pocket to the insert depth
4. If through-hole requested: add a second smaller circle (screw clearance) and pocket through-all

## Clearance hole diameters (for through-holes below insert)
- M2: 2.4mm, M2.5: 2.9mm, M3: 3.4mm, M4: 4.5mm, M5: 5.5mm
