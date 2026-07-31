# Cutting DXF Exporter - Phase 4 Test Plan

## Test Environment

- Current Autodesk Fusion for Windows.
- Add-in version `0.3.11-phase3`.
- A new parametric Design.
- Default tolerance `0.01 mm`.
- An empty writable output folder.
- A DXF viewer or CAD application that displays layer names.

For each test, inspect `cutting_dxf_export.log`. Unless a test says otherwise,
leave **Delete temporary sketches after export** enabled.

## 1. Simple Rectangular Body

### Model

Create a `200 mm x 100 mm x 18 mm` rectangular solid.

### Procedure

1. Select the body in automatic mode.
2. Select **Analyse and Review**.
3. Confirm the preview reports one outside profile and no through-cuts.
4. Choose **Yes**.

### Expected Result

- One final DXF is created.
- Its filename follows `{component}_{body}`.
- It contains one rectangular profile on `CUT_OUTSIDE`.
- The rectangle measures approximately `200 mm x 100 mm`.
- Its minimum X and Y are approximately `0,0`.
- No `.__fusion_*.dxf` category backup remains after success.
- No `DXF_TEMP_*` sketch remains.

## 2. Panel with a Circular Through-Hole

### Model

Create a `200 mm x 100 mm x 18 mm` panel with a `20 mm` circular cut through
the entire body.

### Expected Result

- Analysis reports one outside profile and one confirmed through-cut.
- The final DXF has `CUT_OUTSIDE` and `CUT_INSIDE` layers.
- The rectangle is on `CUT_OUTSIDE`.
- The hole remains a circle and is on `CUT_INSIDE`.
- The hole is not approximated by line segments.

## 3. Panel with a Through-Slot

### Model

Create a panel containing a fully through obround slot made from lines and
tangent arcs.

### Expected Result

- The slot is reported as one confirmed through-cut.
- Slot lines and arcs are preserved on `CUT_INSIDE`.
- No duplicate coincident slot entities are present.

## 4. Through-Hole with a Split Rear Plane

### Model

Create a panel with a circular through-hole and other rear-side geometry that
splits the rear support plane into multiple coplanar B-Rep faces.

### Expected Result

- The circular opening is reported as one confirmed through-cut.
- Its wall may terminate at any coplanar face on the rear support plane.
- The circle appears on `CUT_INSIDE` in the final DXF.
- No `INNER_LOOP_NOT_CONFIRMED_THROUGH` warning is produced for the circle.

## 5. Panel with a Shallow Pocket

### Model

Create an `18 mm` panel with a fully enclosed flat-bottom pocket `6 mm` deep.
Use manual mode to select the pocketed face if automatic mode selects the other
side.

### Expected Result

- Analysis reports one front pocket at approximately `6 mm`.
- The DXF contains `FRONT_POCKET_6MM`.
- The pocket opening is on that layer and retains its original curve types.
- CSV and JSON report the precise measured depth when enabled.

## 6. Two Different Pocket Depths

Create one panel with separate `4 mm` and `6.35 mm` flat-bottom pockets.

### Expected Result

- Analysis reports two front pockets with distinct depths.
- The DXF contains `FRONT_POCKET_4MM` and `FRONT_POCKET_6.35MM`.
- Disabling depth-specific names groups both onto `FRONT_POCKET`.

## 7. Straight Counterbore / Stepped Bore

Create a panel with two normal through-holes and two straight counterbores.
Each counterbore has a `3 mm` deep annular floor and a smaller concentric bore
that continues through the rear face.

### Expected Result

- Analysis reports two `FRONT_POCKET` operations at `3 mm`.
- Analysis reports four `CUT_INSIDE` operations in total.
- Both large pocket circles appear on `FRONT_POCKET_3MM`.
- Both nested small circles and both ordinary holes appear on `CUT_INSIDE`.
- No nested boundary is silently discarded.

## 8. Simple Front Edge Rebate

Create a straight `4 mm` deep rebate open to one outside edge.

### Expected Result

- Analysis reports one front rebate at approximately `4 mm`.
- The rebate boundary appears on `FRONT_REBATE_4MM`.
- With the default `Rebate offset` of `0.3 mm`, rebate edges touching
  `CUT_OUTSIDE` extend outward by `5 mm`.
- Every rebate edge not touching `CUT_OUTSIDE` expands outward by `0.3 mm`.
- A rebate touching two outside edges receives two `5 mm` extensions.
- A rebate touching three outside edges receives three `5 mm` extensions and
  applies the configurable offset only to its remaining edge.
- Setting `Rebate offset` to `0 mm` restores the exact detected rebate boundary.
- `CUT_OUTSIDE` is sourced from the largest complete support-plane face.

## 8A. Rear Pocket and Rebate Export

Create a panel with a `6 mm` pocket and a `4 mm` edge rebate machined from the
rear face. Select the intended front face manually so the machining remains on
the rear side.

### Expected Result

- Analysis reports `BACK_POCKET_6MM` and `BACK_REBATE_4MM`.
- With `Include back` cleared, neither rear layer is in the DXF.
- With `Include back` selected, both rear layers are in the DXF.
- Rear geometry appears as seen through the panel from the selected front view.
- The rear rebate uses the same `5 mm` touching-edge extension and configurable
  offset as a front rebate.
- Front machining and through-cut layers remain unchanged.

## 9. Automatic Split-Front Orientation

Create a `15 mm` panel with a plain rear face, a machined front split into
multiple coplanar faces, a `5 mm` pocket, and a `7.5 mm` edge rebate.

### Expected Result

- Automatic mode reports `AUTOMATIC_FRONT_SIDE_SWITCHED`.
- The selected front normal points outward from the visibly machined side.
- Analysis reports `FRONT_POCKET_5MM` and `FRONT_REBATE_7.5MM`.
- The DXF is viewed from the machined front rather than mirrored from the rear.
- Exactly one complete `CUT_OUTSIDE` profile is exported.

## 10. Arbitrarily Rotated Body

### Model

Rotate a panel around at least two world axes, for example `27 degrees` around
X and `41 degrees` around Z.

### Expected Result

- Front/rear faces and thickness are still detected.
- The exported profile is a true top view of the selected face.
- The outside-profile minimum remains near `0,0`.
- Repeating the export produces the same orientation.
- The filename receives `_2` rather than overwriting the first DXF.

## 11. Two Selected Bodies

### Model

Create separate named bodies of `18 mm` and `12 mm` thickness. Add a through
hole to one body.

### Expected Result

- The review contains a separate section for each body.
- With `One DXF per body` checked, two final DXFs are created.
- Each body uses its own thickness and geometry.
- The hole appears only in the relevant body's `CUT_INSIDE` layer.
- Failure of one body does not prevent the other from exporting.

Clear `One DXF per body` and export the same selection again.

- One `Combined_Selected_Bodies.dxf` is created in the design folder.
- Both outside profiles appear on `CUT_OUTSIDE`.
- The bodies are arranged left-to-right with a `10 mm` gap.
- The hole remains on `CUT_INSIDE` and aligned with its source body.
- No material or thickness suffix is added to the combined filename.

## 12. Manual Face Validation

1. Select two bodies.
2. Choose manual front-face mode.
3. Select fewer than two faces or two faces from the same body.

### Expected Result

- **Analyse and Review** remains disabled.
- Selecting exactly one planar face per body enables it.

## 13. Operator Cancellation

1. Run a valid analysis.
2. Choose **No** in the review confirmation.

### Expected Result

- No DXF or temporary category DXF is created.
- No temporary sketch is created.
- The session log records cancellation after review.

## 14. Temporary Sketch Retention

1. Disable **Delete temporary sketches after export**.
2. Export a panel with a through-hole.

### Expected Result

- `DXF_TEMP_CUT_OUTSIDE` remains in the root component.
- `DXF_TEMP_CUT_INSIDE` remains in the root component.
- Both contain only their operation-category geometry.
- Their geometry shares the same rotation and translation.
- Delete these test sketches manually before continuing.

## 15. Post-Processing Failure Backup

This test requires temporarily forcing the post-processor to fail or using a
Fusion build that emits unsupported binary DXF.

### Expected Result

- No unvalidated final DXF replaces an existing file.
- Unmodified category files such as
  `Part.__fusion_CUT_OUTSIDE.dxf` remain.
- The result dialog and log identify the failure and backup paths.
- Temporary sketches are still cleaned when cleanup is enabled.

## 16. Existing Filename

Export the same unchanged body twice to the same folder.

### Expected Result

- The first file uses the requested name.
- The second file receives `_2`.
- The first file is not overwritten.

## 17. CSV and JSON Reports

1. Enable **Write analysis CSV** and **Write diagnostic JSON**.
2. Export a body containing one through-hole, one pocket, and one rebate.

### Expected Result

- `cutting_dxf_analysis.csv` contains one row per detected operation.
- Depth, thickness, layer, confidence, warning, DXF path, and UTC date are
  populated as applicable.
- `cutting_dxf_analysis.json` contains complete analyses and export results.
- No `.tmp` report remains after successful publication.

## 18. VCarve Pro Compatibility

1. Export a body that creates at least four custom DXF layers.
2. Open the final DXF directly in VCarve Pro.

### Expected Result

- No `AcDbLayerTableRecord: Record name is empty` warnings appear.
- Every custom layer name is present.
- Every merged entity has a unique hexadecimal handle.
- `$HANDSEED` is greater than every generated object handle.

## 19. Material Folder and Thickness Filename

1. Assign `Birch Plywood` to a `15 mm` body.
2. Assign `MDF` to a separate `18.5 mm` body.
3. Save the Fusion design as `Kitchen Project` and ensure Fusion displays a
   suffix such as `Kitchen Project v3`.
4. Export both using `{component}_{body}`.

### Expected Result

- A `Kitchen Project` folder is created in the selected output folder.
- No `Kitchen Project v3` folder is created.
- The first DXF is under `Kitchen Project\Birch Plywood` and ends in
  `_15mm.dxf`.
- The second DXF is under `Kitchen Project\MDF` and ends in `_18.5mm.dxf`.
- Invalid Windows filename characters in material names are replaced safely.
- An unassigned body uses the `Unspecified Material` folder.
- The log, CSV, and JSON are directly inside `Kitchen Project`.
- Exporting again replaces each matching DXF instead of creating an `_2` copy.
- Unrelated DXFs already in the material folder remain unchanged.

## 20. Full-Thickness Planar Mitre

Create a rectangular constant-thickness panel with one straight `45 degree`
mitred edge. Ensure one support face represents the panel's largest dimensions.

### Expected Result

- `CUT_OUTSIDE` follows the panel at its largest projected dimensions.
- Analysis reports one mitre guide.
- The DXF contains a `MITRE_90DEG` layer with one line.
- The line is parallel to and `0.5 mm` outside the mitred outline edge.
- It extends `2 mm` beyond each end of that edge.
- Ordinary square side faces do not create mitre guides.

Repeat with two perpendicular `45 degree` mitred edges sharing one panel corner.

- Both lines remain `0.5 mm` outside their respective mitred edges.
- Their shared-corner endpoints meet at the offset-line intersection.
- Neither line receives the `2 mm` extension at the shared corner.
- Their unconnected endpoints still extend by `2 mm`.

Repeat with two mitred edges of different angles sharing one panel corner.

- Each guide is exported on its corresponding angle-specific layer.
- Both guides retain the `2 mm` extension at the shared corner.
- Neither guide is trimmed to the offset-line intersection.

Set `Mitre guide offset` to `1 mm` and export again.

- Every mitre guide is now `1 mm` outside its source edge.
- Joined mitre guides still meet at their recalculated offset-line intersection.
- Restoring the field to `0.5 mm` restores the original guide position.
- A measured `22.5 degree` mitre uses the calculated layer `MITRE_135DEG`.
- The final DXF contains a `markups` layer with one centred text entity.
- The text exactly matches the final DXF filename, including `.dxf`.

## 21. Duplicate Component Occurrences

1. Create a component named `Speaker_Table_Legs` containing at least one body.
2. Insert or duplicate it so the browser shows `Speaker_Table_Legs:1` and
   `Speaker_Table_Legs:2`.
3. Select the equivalent body from both occurrences.
4. Export using `{component}_{body}` with `One DXF per body` checked.

### Expected Result

- Both occurrences produce a DXF.
- The filenames contain `Speaker_Table_Legs_1` and `Speaker_Table_Legs_2`.
- The colon is replaced with an underscore for Windows compatibility.
- Neither occurrence overwrites the other occurrence's DXF.
- Native, non-occurrence bodies continue to use their component name.

## 22. Load and Unload

1. Start the add-in twice.
2. Confirm only one toolbar control exists.
3. Stop the add-in.
4. Confirm its command control disappears.
5. Start it again and repeat Test 1.

## Acceptance Criteria

- Simple outside profiles, circular holes, and through-slots export correctly.
- Simple flat-bottom front pockets and rebates receive measured-depth layers.
- Blind pockets are not mislabeled as through-cuts.
- CSV and JSON contain complete serializable operation data when enabled.
- Required DXF layers exist and contain the expected entities.
- Curves are preserved by Fusion projection/export where supported.
- Rotated bodies export in repeatable face-local orientation near origin.
- Existing files are not overwritten.
- One failed body does not stop another body.
- Original design geometry remains unchanged.
- Temporary sketches are cleaned on success and failure when enabled.
- Full exceptions and cleanup decisions appear in the session log.
