# Cutting DXF Exporter

Cutting DXF Exporter is a Windows Autodesk Fusion add-in that derives
manufacturing DXF files from finished solid B-Rep geometry.

The current release implements **Phase 3 - Front Pockets and Rebates**. It
exports the principal outside profile, confirmed through-cuts, simple
flat-bottom front pockets, and simple front edge rebates from final topology.
Rear machining and uncertain boundaries remain review-only.

## Phase 3 Features

- Adds **Export Cutting DXFs** to the Design workspace Add-Ins panel.
- Accepts one or more finished solid `BRepBody` selections.
- Supports automatic, manual, and automatic-with-review front-face selection.
- Starts automatic selection from the largest bounded planar face, compares
  both support sides, and prefers the side with stronger front-machining
  evidence.
- Treats split coplanar faces as one manufacturing support side.
- Measures thickness perpendicular to the manufacturing faces.
- Checks body vertices and face sample points against the support-plane slab.
- Uses `BRepLoop.isOuter`; it never assumes loop ordering.
- Classifies the front outer loop as `CUT_OUTSIDE`.
- Classifies an inner loop as `CUT_INSIDE` only when every adjacent wall can be
  traced directly to a coplanar face on the selected rear support plane.
- Leaves every unconfirmed inner loop as `UNKNOWN` with a reason.
- Detects enclosed pockets only when every supported vertical wall terminates
  at one same-facing planar floor.
- Detects straight nested bores that begin at a confirmed pocket floor and
  terminate at the rear support plane.
- Detects planar full-thickness mitre faces and creates calculated-angle guide
  layers such as `MITRE_90DEG` from the largest panel outline.
- Detects front edge rebates when a supported wall from the front outer
  boundary terminates at a recessed same-facing planar floor.
- Measures pocket and rebate depth perpendicular to the front face.
- Creates depth layers such as `FRONT_POCKET_6MM` and
  `FRONT_REBATE_4MM`, with an option to omit depth suffixes.
- Places each mitre guide on its angle-specific `MITRE_*DEG` layer, offset
  `0.5 mm` outward from the panel edge. Free endpoints extend `2 mm`; adjacent
  guides with the same mitre angle meet at their offset-line intersection
  without extension, while guides with different angles retain the extension.
- Provides a unit-aware `Mitre guide offset` command input, defaulting to
  `0.5 mm`, so the guide distance can be changed for each export.
- Adds the final DXF filename as centred text on a `markups` layer.
- Removes Fusion's trailing document version, such as `v3`, from the top-level
  design folder name.
- Atomically overwrites an existing DXF when its generated material folder and
  filename match the new export.
- Requires a Yes/No operator confirmation after showing the analysis.
- Creates separate temporary sketches for every required operation/depth layer.
- Uses `Sketch.project2(..., False)` where available to preserve lines, circles,
  arcs, ellipses, and splines as native sketch curves.
- Uses the stable face X axis, then moves the outside-profile minimum to `0,0`.
- Exports category DXFs through
  `ExportManager.createDXFSketchExportOptions` and `ExportManager.execute`.
- Parses ASCII DXF as group-code/value pairs and merges category entities.
- Creates and validates cutting and front-machining layers.
- Publishes the final DXF only after layer and entity-count validation succeeds.
- Keeps Fusion category DXFs as backups when merge or validation fails.
- Uses a numeric suffix rather than overwriting an existing output file.
- Creates a sanitized Fusion-design-name folder, then groups body DXFs into
  physical-material subfolders inside it.
- Appends detected material thickness to every DXF filename, for example
  `Component_Panel_15mm.dxf`.
- Deletes temporary sketches in a `finally` cleanup path unless disabled.
- Continues with other selected bodies when one body fails.
- Writes full decisions, output paths, exceptions, and cleanup results to
  `cutting_dxf_export.log`.
- Optionally writes `cutting_dxf_analysis.csv` and
  `cutting_dxf_analysis.json` using atomic replacement.

## Project Structure

```text
CuttingDXFExporter/
|-- CuttingDXFExporter.py
|-- CuttingDXFExporter.manifest
|-- README.md
|-- TEST_PLAN.md
|-- commands/
|   |-- __init__.py
|   `-- export_cutting_dxf.py
|-- analysis/
|   |-- __init__.py
|   |-- body_analyser.py
|   |-- coordinate_system.py
|   |-- face_analyser.py
|   `-- feature_classifier.py
|-- export/
|   |-- __init__.py
|   |-- dxf_exporter.py
|   |-- dxf_postprocessor.py
|   `-- sketch_builder.py
|-- models/
|   |-- __init__.py
|   `-- analysis_models.py
|-- resources/
|   `-- ExportCuttingDXF/
|       |-- 16x16.png
|       |-- 32x32.png
|       `-- 64x64.png
`-- utilities/
    |-- __init__.py
    |-- file_utils.py
    |-- fusion_utils.py
    |-- geometry_utils.py
    |-- layer_utils.py
    |-- logging_utils.py
    `-- reporting_utils.py
```

## Windows Installation

1. Stop the old add-in or close Fusion.
2. Press `Win+R`.
3. Enter `%appdata%\Autodesk\Autodesk Fusion\API\AddIns`.
4. Copy the complete `CuttingDXFExporter` folder into that `AddIns` folder.
5. Start Fusion and open a Design.
6. Choose **Utilities > Add-Ins > Scripts and Add-Ins**.
7. Select **CuttingDXFExporter** on the Add-Ins tab and choose **Run**.
8. Optionally enable **Run on Startup**.
9. Find **Export Cutting DXFs** in the Design workspace Add-Ins panel.

For development, keep the folder anywhere and use the `+` command in
**Scripts and Add-Ins** to link the folder from the device.

## Using Phase 3

1. Open a Design containing one or more finished flat solid bodies.
2. Run **Export Cutting DXFs**.
3. Select the bodies.
4. Choose an existing writable output folder.
5. Choose the front-face mode.
6. Set a filename format using `{component}` and `{body}`.
   The detected thickness suffix is added automatically.
7. Leave the analysis tolerance at `0.01 mm` unless a justified model
   tolerance requires another value.
8. Choose whether to include front machining and depth-specific layer names.
9. Optionally enable CSV and diagnostic JSON reports.
10. Choose whether temporary sketches should be deleted.
11. Select **Analyse and Review**.
12. Review every body, especially pocket/rebate depths, `UNKNOWN` boundaries,
    and warnings.
13. Choose **Yes** to export known geometry or **No** to cancel without writing
    DXFs.
14. Review the final per-body result dialog and
    `cutting_dxf_export.log`.

`UNKNOWN` geometry is never exported in Phase 3.

## Output Folders and Filenames

The selected output folder is treated as the parent destination. The add-in
creates a folder named from the current Fusion design filename without a
trailing Fusion version such as ` v3`, followed by a physical-material folder.
The final path is:

```text
<selected output>\<Fusion filename>\<material>\<name>_<thickness>mm.dxf
```

Example:

```text
C:\Cutting DXFs\Kitchen Project\Birch Plywood\Cabinet_Side_15mm.dxf
```

Design and material names are sanitized for Windows folder rules. If no body
material is available, the component material is used; if neither is
available, the folder is `Unspecified Material`. Thickness is rounded to at
most three decimal places, so values such as `15 mm` and `18.5 mm` become
`_15mm` and `_18.5mm`.

The following session files are written directly in the version-free
Fusion-design folder, alongside the material folders:

- `cutting_dxf_export.log`
- `cutting_dxf_analysis.csv`, when enabled
- `cutting_dxf_analysis.json`, when enabled

If the generated material folder already contains a DXF with the same filename,
the completed new DXF atomically replaces it. Unrelated DXFs are not changed.

## Geometry and Thickness Algorithm

The body orientation in world space is irrelevant:

1. Collect faces whose underlying surface casts to `adsk.core.Plane`.
2. Obtain each outward normal from the bounded face evaluator.
3. In automatic mode, choose the planar face with the largest bounded area.
4. Find the furthest planar face behind it whose outward normal is opposed
   within `0.5 degrees`.
5. Collect all same-facing coplanar fragments on both support planes.
6. Classify both possible front orientations and prefer the side containing
   more confidently detected front pockets and rebates.
7. Measure signed plane distance between the two support planes.
8. Check body vertices and one interior sample point from every face against
   the resulting support-plane slab.

This is a conservative sheet-envelope check. It is not proof that every point
contains full-thickness material because valid holes and pockets remove local
material.

## Through-Cut Classification

For each inner loop on the selected front face:

1. Inspect every loop edge.
2. Find the adjacent face other than the front face.
3. Treat those adjacent faces as candidate walls.
4. Collect all opposed coplanar faces on the selected rear support plane.
5. Require every candidate wall to share an edge with that rear face set.
6. Classify the loop as `CUT_INSIDE` only when all checks pass.

This intentionally rejects multi-depth wall chains and ambiguous topology. A
rear support plane split into several coplanar B-Rep faces is supported.

## Front Pocket and Rebate Classification

For an enclosed front loop, the classifier:

1. Rejects it as a pocket if its walls already reach the rear support plane.
2. Accepts only planar walls perpendicular to the front normal or cylindrical
   walls whose axis is parallel to the front normal.
3. Requires every wall to terminate at the same planar floor.
4. Requires the floor normal to face the same direction as the front face.
5. Requires depth to be greater than tolerance and less than material
   thickness by more than tolerance.

The same floor-depth checks are applied to recessed geometry reached from the
front outer boundary. A qualifying open-edge feature is classified as
`FRONT_REBATE`. Angled, non-planar, multi-floor, and near-through features
remain `UNKNOWN`.

After confirming a pocket, each inner loop on its planar floor is inspected.
If every adjacent nested wall reaches the rear support plane, that smaller
boundary is also exported as `CUT_INSIDE`. This supports unambiguous
straight-sided counterbores and stepped bores without treating conical
countersinks as pockets.

## Coordinate Convention

The selected front face becomes the exported XY plane. The local Z direction
is its outward normal.

The longest straight outer-loop edge defines local X where possible. Its sign
is canonicalized for repeatable exports. If no suitable line exists, a stable
projected global axis is used. Exported sketch geometry is rotated to that
axis, and the minimum X and Y of `CUT_OUTSIDE` are translated to `0,0`.

Rear export is not implemented yet. The fixed future convention remains: the
DXF is viewed from the front, and rear machining will appear as seen through
the part from that front view.

## Mitre Guide Classification

A side face is treated as a mitre only when:

1. Its underlying surface is planar.
2. Its normal is neither parallel nor perpendicular to the manufacturing-face
   normal within tolerance.
3. It shares topology with both front and rear support-plane face sets.
4. Its edge on the largest support-plane outline is straight.

The acute angle between the mitre face and the manufacturing support plane is
calculated from their face normals. The layer suffix is then calculated as
`180 - (measured angle x 2)` and rounded to at most three decimal places. A
measured `45 degree` mitre therefore uses `MITRE_90DEG`; a measured
`22.5 degree` mitre uses `MITRE_135DEG`.

The largest support face remains the source for `CUT_OUTSIDE`. The straight
mitre edge is projected separately and offset outward from the outside-profile
centre by the `Mitre guide offset` command value, which defaults to `0.5 mm`.
A free endpoint is extended along the line axis by `2 mm`. When two detected
mitre edges with the same angle share an endpoint, their offset lines are
intersected and both guides terminate at that common point instead. Meeting
guides with different mitre angles retain their `2 mm` extension. Each generated
line is exported on its angle-specific `MITRE_*DEG` layer.

After the operation DXFs are merged, the final filename, including its
`.dxf` extension, is added as horizontally and vertically centred DXF text.
The text is positioned from the `CUT_OUTSIDE` bounds and placed on the lowercase
`markups` layer so it can be hidden or assigned a marking toolpath separately.

## DXF Processing

Fusion operation categories are exported separately because sketch entities do
not provide reliable custom DXF layer assignment.

For a split machined front, feature boundaries are collected from every
coplanar front fragment. The principal `CUT_OUTSIDE` loop is sourced from the
largest full support-plane face on either side and projected into the selected
front coordinate system.

The standard-library post-processor:

1. Rejects binary or malformed DXF files.
2. Reads code/value pairs rather than using string replacement.
3. Preserves the base DXF header and non-entity sections.
4. Adds missing `LAYER` table records.
5. Adds R13+ handles and `AcDbSymbolTableRecord` /
   `AcDbLayerTableRecord` subclass markers required by strict importers.
6. Remaps merged entity handles and internal handle references to prevent
   collisions between separately exported Fusion category files.
7. Advances `$HANDSEED` beyond every generated object handle.
8. Assigns every category entity to its required layer.
9. Replaces only the `ENTITIES` section in the base structure.
10. Writes a sibling temporary file.
11. Re-reads it and validates layers, subclass metadata, unique handles, and
    entity counts.
12. Atomically replaces the final output path only after validation.

If this process fails, files named like
`Part.__fusion_CUT_OUTSIDE.dxf` are retained as unmodified Fusion backups.

## Preview API Requirement

`ExportManager.createDXFSketchExportOptions` is currently a Fusion preview API.
The add-in checks for both that factory and `ExportManager.execute`.

If unavailable, analysis still works but export reports a clear per-body error.
There is no `Sketch.saveAsDXF` fallback because that API is retired and is
explicitly excluded by this project.

## Known Phase 3 Limitations

- Rear pockets and rear rebates are not classified or exported.
- Pocket/rebate walls are limited to perpendicular planes and cylinders with
  axes parallel to the front normal.
- Mitre guides are limited to straight edges on planar bevel faces spanning
  the complete material thickness. Curved, partial-depth, compound, and
  non-planar mitres remain unsupported.
- If both support sides are heavily fragmented, the largest available support
  face may still not represent the complete cutting silhouette. Always verify
  `CUT_OUTSIDE` before manufacturing.
- `UNKNOWN` geometry cannot yet be enabled for export.
- Split or unusually segmented through-hole walls may be reported as
  `UNKNOWN` even when they physically pass through.
- Curved main faces and non-constant sheet envelopes are unsupported.
- Chamfers, fillets, countersinks, ambiguous or multi-level counterbores,
  angled floors, side drilling, and 3D machining are not classified.
- Mesh bodies and folded-state sheet-metal extraction are unsupported.
- Only ASCII DXF post-processing is supported.
- Duplicate component-instance geometry is not yet deduplicated.
- Temporary highlighting is not implemented.

## Startup Troubleshooting

Version `0.3.11` loads project modules inside an add-in-specific runtime
namespace. Import failures are shown in Fusion and appended to:

```text
%TEMP%\CuttingDXFExporter_startup.log
```

## Safety Guarantees

The add-in never modifies or deletes original bodies, components, sketches, or
timeline features. Phase 3 temporarily creates root-component sketches and
deletes only the exact sketch objects it created. Disabling cleanup deliberately
leaves those named `DXF_TEMP_*` sketches for inspection.
