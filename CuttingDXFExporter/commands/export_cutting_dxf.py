"""Fusion UI command for Phase 4 analysis and cutting DXF export."""

import json
import os
from typing import Any, List, Optional

import adsk.core
import adsk.fusion

from ..analysis.body_analyser import (
    DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    analyse_body,
)
from ..analysis.face_analyser import (
    collect_coplanar_support_faces,
    collect_planar_faces,
    largest_planar_face,
    record_for_manual_face,
)
from ..export.dxf_exporter import (
    export_phase_four_bodies_combined,
    export_phase_three_body,
)
from ..models.analysis_models import BodyAnalysis, ExportResult
from ..utilities.file_utils import (
    default_output_folder,
    design_output_folder,
    render_body_filename,
    validate_output_folder,
)
from ..utilities.fusion_utils import (
    active_design,
    body_component_name,
    body_display_name,
    body_identity,
    body_material_name,
    design_name,
    entity_token,
    format_analysis_summary,
    selected_bodies,
    show_error,
    temp_id,
    temporary_union_body,
)
from ..utilities.logging_utils import (
    configure_session_logger,
    finish_session,
    get_logger,
)
from ..utilities.reporting_utils import (
    write_analysis_csv,
    write_analysis_json,
)

COMMAND_ID = "CuttingDXFExporter_ExportCuttingDXFs"
ADDIN_VERSION = "0.6.2-persisted-union-export"
COMMAND_NAME = "Export Cutting DXFs (v0.6.2)"
COMMAND_DESCRIPTION = (
    "Analyse finished solid bodies before creating manufacturing DXF files. "
    f"Build {ADDIN_VERSION}."
)
WORKSPACE_ID = "FusionSolidEnvironment"
PANEL_ID = "SolidScriptsAddinsPanel"
INITIAL_DIALOG_WIDTH = 430
INITIAL_DIALOG_HEIGHT = 700
MINIMUM_DIALOG_WIDTH = 380
MINIMUM_DIALOG_HEIGHT = 550

BODY_INPUT_ID = "selected_bodies"
VERSION_INPUT_ID = "addin_version"
OUTPUT_FOLDER_INPUT_ID = "output_folder"
BROWSE_INPUT_ID = "browse_output_folder"
FACE_MODE_INPUT_ID = "face_selection_mode"
MANUAL_FACE_INPUT_ID = "manual_front_faces"
TOLERANCE_INPUT_ID = "analysis_tolerance"
DELETE_TEMP_INPUT_ID = "delete_temp"
FILENAME_INPUT_ID = "filename_format"
OPEN_FOLDER_INPUT_ID = "open_folder"
INCLUDE_FRONT_INPUT_ID = "include_front"
INCLUDE_REAR_INPUT_ID = "include_rear"
ONE_DXF_PER_BODY_INPUT_ID = "one_dxf_per_body"
MERGE_BODIES_INPUT_ID = "merge_bodies"
MERGE_FACE_INPUT_ID = "merge_reference_face"
WRITE_CSV_INPUT_ID = "write_csv"
WRITE_JSON_INPUT_ID = "write_json"
DEPTH_LAYERS_INPUT_ID = "depth_layers"
MITRE_OFFSET_INPUT_ID = "mitre_offset"
REBATE_OFFSET_INPUT_ID = "rebate_offset"

FACE_MODES = (
    "Automatic: largest planar face",
    "Manual: user selects the front face",
    "Automatic with review",
)

_handlers: List[Any] = []


def start() -> None:
    """Create the command definition and add it to Fusion's Design workspace."""

    application = adsk.core.Application.get()
    ui = application.userInterface
    existing_control = _command_control(ui)
    if existing_control:
        existing_control.deleteMe()
    existing_definition = ui.commandDefinitions.itemById(COMMAND_ID)
    if existing_definition:
        existing_definition.deleteMe()

    resource_folder = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "resources", "ExportCuttingDXF")
    )
    definition = ui.commandDefinitions.addButtonDefinition(
        COMMAND_ID,
        COMMAND_NAME,
        COMMAND_DESCRIPTION,
        resource_folder,
    )
    if not definition:
        raise RuntimeError("Fusion did not create the command definition.")

    created_handler = CommandCreatedHandler()
    definition.commandCreated.add(created_handler)
    _handlers.append(created_handler)

    panel = _toolbar_panel(ui)
    if not panel:
        definition.deleteMe()
        raise RuntimeError(
            "The Design workspace Add-Ins panel was not found. "
            "Open a Design document and restart the add-in."
        )
    control = panel.controls.addCommand(definition)
    if not control:
        definition.deleteMe()
        raise RuntimeError("Fusion did not create the toolbar control.")
    control.isPromotedByDefault = True
    control.isPromoted = True


def stop() -> None:
    """Remove all UI objects and release event-handler references."""

    application = adsk.core.Application.get()
    ui = application.userInterface if application else None
    if ui:
        control = _command_control(ui)
        if control:
            control.deleteMe()
        definition = ui.commandDefinitions.itemById(COMMAND_ID)
        if definition:
            definition.deleteMe()
    _handlers.clear()


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    """Create command inputs and attach command-lifetime handlers."""

    def notify(self, args: adsk.core.CommandCreatedEventArgs) -> None:
        try:
            command = args.command
            command.isRepeatable = False
            command.okButtonText = "Analyse and Review"
            command.setDialogMinimumSize(
                MINIMUM_DIALOG_WIDTH,
                MINIMUM_DIALOG_HEIGHT,
            )
            command.setDialogInitialSize(
                INITIAL_DIALOG_WIDTH,
                INITIAL_DIALOG_HEIGHT,
            )
            inputs = command.commandInputs

            inputs.addTextBoxCommandInput(
                VERSION_INPUT_ID,
                "Add-in build",
                ADDIN_VERSION,
                1,
                True,
            )

            output_folder = inputs.addStringValueInput(
                OUTPUT_FOLDER_INPUT_ID,
                "Output folder",
                default_output_folder(),
            )
            output_folder.tooltip = (
                (
                    "A Fusion-design folder is created here without its version "
                    "suffix, containing material folders, DXFs, logs, and "
                    "optional reports."
                )
            )
            inputs.addBoolValueInput(
                BROWSE_INPUT_ID,
                "Browse for output folder",
                False,
                "",
                False,
            )

            filename = inputs.addStringValueInput(
                FILENAME_INPUT_ID,
                "Filename format",
                "{component}_{body}",
            )
            filename.tooltip = "Supported fields: {component} and {body}."

            inputs.addBoolValueInput(
                OPEN_FOLDER_INPUT_ID,
                "Open output folder",
                True,
                "",
                False,
            )
            inputs.addBoolValueInput(
                DEPTH_LAYERS_INPUT_ID,
                "Include detected depth in layer names",
                True,
                "",
                True,
            )

            face_mode = inputs.addDropDownCommandInput(
                FACE_MODE_INPUT_ID,
                "Face-selection mode",
                adsk.core.DropDownStyles.TextListDropDownStyle,
            )
            for index, mode in enumerate(FACE_MODES):
                face_mode.listItems.add(mode, index == 1)

            inputs.addBoolValueInput(
                INCLUDE_FRONT_INPUT_ID,
                "Include front",
                True,
                "",
                True,
            )
            inputs.addBoolValueInput(
                INCLUDE_REAR_INPUT_ID,
                "Include back",
                True,
                "",
                True,
            )
            one_dxf = inputs.addBoolValueInput(
                ONE_DXF_PER_BODY_INPUT_ID,
                "One DXF per body",
                True,
                "",
                True,
            )
            one_dxf.isEnabled = True
            one_dxf.tooltip = (
                "Uncheck to arrange all selected bodies left-to-right in one "
                "combined DXF."
            )
            merge_bodies = inputs.addBoolValueInput(
                MERGE_BODIES_INPUT_ID,
                "Merge Bodies",
                True,
                "",
                False,
            )
            merge_bodies.tooltip = (
                "Temporarily union copies of exactly two touching bodies, then "
                "analyse and export the combined solid. The originals are unchanged."
            )

            bodies = inputs.addSelectionInput(
                BODY_INPUT_ID,
                "Solid bodies",
                "Select one or more finished solid B-Rep bodies.",
            )
            bodies.addSelectionFilter("SolidBodies")
            bodies.setSelectionLimits(1, 0)

            manual_faces = inputs.addSelectionInput(
                MANUAL_FACE_INPUT_ID,
                "Manual faces",
                "Select one planar front face for every selected body.",
            )
            manual_faces.addSelectionFilter("PlanarFaces")
            manual_faces.setSelectionLimits(0, 0)
            manual_faces.isVisible = True

            merge_face = inputs.addSelectionInput(
                MERGE_FACE_INPUT_ID,
                "Merge reference face",
                "Select one planar front face from either selected body.",
            )
            merge_face.addSelectionFilter("PlanarFaces")
            merge_face.setSelectionLimits(0, 1)
            merge_face.isVisible = False

            inputs.addBoolValueInput(
                DELETE_TEMP_INPUT_ID,
                "Delete temporary sketches",
                True,
                "",
                True,
            )
            inputs.addBoolValueInput(
                WRITE_CSV_INPUT_ID,
                "Write CSV",
                True,
                "",
                False,
            )
            inputs.addBoolValueInput(
                WRITE_JSON_INPUT_ID,
                "Write JSON",
                True,
                "",
                False,
            )
            include_unknown = inputs.addBoolValueInput(
                "include_unknown",
                "Include unknown geometry",
                True,
                "",
                False,
            )
            include_unknown.isEnabled = False
            include_unknown.isVisible = False

            mitre_offset = inputs.addValueInput(
                MITRE_OFFSET_INPUT_ID,
                "Mitre Guide Offset",
                "mm",
                adsk.core.ValueInput.createByString("0.5 mm"),
            )
            mitre_offset.minimumValue = 0.0
            mitre_offset.isMinimumInclusive = True
            mitre_offset.tooltip = (
                "Outward distance from each detected mitre edge to its MITRE guide."
            )
            rebate_offset = inputs.addValueInput(
                REBATE_OFFSET_INPUT_ID,
                "Rebate Offset",
                "mm",
                adsk.core.ValueInput.createByString("0.3 mm"),
            )
            rebate_offset.minimumValue = 0.0
            rebate_offset.isMinimumInclusive = True
            rebate_offset.tooltip = (
                "Edges touching the outside profile extend by 5 mm. Other rebate "
                "edges use this offset. Set to 0 mm to export the detected size "
                "unchanged."
            )

            tolerance = inputs.addValueInput(
                TOLERANCE_INPUT_ID,
                "Analysis tolerance",
                "mm",
                adsk.core.ValueInput.createByString("0.01 mm"),
            )
            tolerance.minimumValue = 0.000001
            tolerance.isMinimumInclusive = True
            tolerance.isVisible = False

            input_changed_handler = InputChangedHandler()
            validate_handler = ValidateInputsHandler()
            execute_handler = ExecuteHandler()
            command.inputChanged.add(input_changed_handler)
            command.validateInputs.add(validate_handler)
            command.execute.add(execute_handler)
            _handlers.extend(
                [input_changed_handler, validate_handler, execute_handler]
            )
        except Exception as error:
            logger = get_logger()
            logger.exception("Command creation failed.")
            application = adsk.core.Application.get()
            show_error(
                application.userInterface if application else None,
                "Cutting DXF Exporter",
                error,
            )


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    """Handle folder browsing and manual-face mode visibility."""

    def notify(self, args: adsk.core.InputChangedEventArgs) -> None:
        try:
            changed = args.input
            command_inputs = changed.commandInputs
            if changed.id == BROWSE_INPUT_ID and changed.value:
                self._browse(command_inputs)
                changed.value = False
            elif changed.id in {FACE_MODE_INPUT_ID, MERGE_BODIES_INPUT_ID}:
                self._update_selection_controls(command_inputs)
        except Exception:
            get_logger().exception("Input-changed handler failed.")

    @staticmethod
    def _browse(command_inputs: adsk.core.CommandInputs) -> None:
        application = adsk.core.Application.get()
        dialog = application.userInterface.createFolderDialog()
        dialog.title = "Select Cutting DXF Output Folder"
        output_input = command_inputs.itemById(OUTPUT_FOLDER_INPUT_ID)
        current_path = output_input.value.strip()
        if os.path.isdir(current_path):
            dialog.initialDirectory = current_path
        if dialog.showDialog() == adsk.core.DialogResults.DialogOK:
            output_input.value = dialog.folder

    @staticmethod
    def _update_selection_controls(command_inputs: adsk.core.CommandInputs) -> None:
        merge_enabled = command_inputs.itemById(MERGE_BODIES_INPUT_ID).value
        mode_input = command_inputs.itemById(FACE_MODE_INPUT_ID)
        mode = mode_input.selectedItem.name if mode_input.selectedItem else ""
        mode_input.isEnabled = not merge_enabled
        command_inputs.itemById(MANUAL_FACE_INPUT_ID).isVisible = (
            not merge_enabled and mode == FACE_MODES[1]
        )
        command_inputs.itemById(MERGE_FACE_INPUT_ID).isVisible = merge_enabled
        command_inputs.itemById(ONE_DXF_PER_BODY_INPUT_ID).isEnabled = (
            not merge_enabled
        )


class ValidateInputsHandler(adsk.core.ValidateInputsEventHandler):
    """Prevent analysis when required inputs are incomplete or inconsistent."""

    def notify(self, args: adsk.core.ValidateInputsEventArgs) -> None:
        try:
            inputs = args.inputs
            body_input = inputs.itemById(BODY_INPUT_ID)
            if body_input.selectionCount < 1:
                args.areInputsValid = False
                return
            merge_enabled = inputs.itemById(MERGE_BODIES_INPUT_ID).value
            if merge_enabled:
                if body_input.selectionCount != 2:
                    args.areInputsValid = False
                    return
                merge_face_input = inputs.itemById(MERGE_FACE_INPUT_ID)
                if merge_face_input.selectionCount != 1:
                    args.areInputsValid = False
                    return
                merge_face = adsk.fusion.BRepFace.cast(
                    merge_face_input.selection(0).entity
                )
                bodies = selected_bodies(body_input)
                if not merge_face or not any(
                    _face_belongs_to_body(merge_face, body) for body in bodies
                ):
                    args.areInputsValid = False
                    return
            output_folder = inputs.itemById(OUTPUT_FOLDER_INPUT_ID).value
            validate_output_folder(output_folder)
            filename_template = inputs.itemById(FILENAME_INPUT_ID).value.strip()
            if not filename_template:
                args.areInputsValid = False
                return
            render_body_filename(filename_template, "Component", "Body")

            mode_input = inputs.itemById(FACE_MODE_INPUT_ID)
            mode = mode_input.selectedItem.name if mode_input.selectedItem else ""
            if not merge_enabled and mode == FACE_MODES[1]:
                manual_input = inputs.itemById(MANUAL_FACE_INPUT_ID)
                bodies = selected_bodies(body_input)
                if manual_input.selectionCount != len(bodies):
                    args.areInputsValid = False
                    return
                if not _manual_faces_cover_bodies(manual_input, bodies):
                    args.areInputsValid = False
                    return
            args.areInputsValid = True
        except Exception:
            args.areInputsValid = False


class ExecuteHandler(adsk.core.CommandEventHandler):
    """Analyse, request approval, and export every body independently."""

    def notify(self, args: adsk.core.CommandEventArgs) -> None:
        application = adsk.core.Application.get()
        ui = application.userInterface
        logger = get_logger()
        outcome = "failed"
        temporary_union = None
        try:
            command_inputs = args.command.commandInputs
            selected_output_folder = validate_output_folder(
                command_inputs.itemById(OUTPUT_FOLDER_INPUT_ID).value
            )
            design = active_design()
            if not design:
                raise RuntimeError("The active product is not a Fusion Design.")
            output_folder = design_output_folder(
                selected_output_folder,
                design_name(design),
            )
            os.makedirs(output_folder, exist_ok=True)
            logger = configure_session_logger(
                output_folder,
                ADDIN_VERSION,
                str(getattr(application, "version", "unknown")),
            )

            body_input = command_inputs.itemById(BODY_INPUT_ID)
            selected_body_list = selected_bodies(body_input)
            mode_input = command_inputs.itemById(FACE_MODE_INPUT_ID)
            mode = mode_input.selectedItem.name if mode_input.selectedItem else FACE_MODES[1]
            manual_input = command_inputs.itemById(MANUAL_FACE_INPUT_ID)
            tolerance_internal = command_inputs.itemById(TOLERANCE_INPUT_ID).value
            merge_bodies = command_inputs.itemById(MERGE_BODIES_INPUT_ID).value
            merge_reference_face = None
            merge_manual_face = None
            merged_component_name = None
            merged_material_name = None
            if merge_bodies:
                merge_face_input = command_inputs.itemById(MERGE_FACE_INPUT_ID)
                merge_reference_face = adsk.fusion.BRepFace.cast(
                    merge_face_input.selection(0).entity
                )
                merged_component_name, merged_material_name = _merge_metadata(
                    selected_body_list
                )
                temporary_union = temporary_union_body(
                    design,
                    selected_body_list,
                )
                combined_body = temporary_union.body
                merge_manual_face = _combined_reference_face(
                    selected_body_list,
                    merge_reference_face,
                    combined_body,
                    tolerance_internal,
                )
                bodies = [combined_body]
            else:
                bodies = selected_body_list

            logger.info(
                "Selected bodies=%d mode=%s tolerance_internal=%s",
                len(selected_body_list),
                mode,
                tolerance_internal,
            )
            analyses = []
            for selection_index, body in enumerate(bodies):
                logger.info(
                    "Analysing selection=%d component=%s body=%s token=%s",
                    selection_index,
                    merged_component_name or getattr(
                        getattr(body, "parentComponent", None),
                        "name",
                        "unknown",
                    ),
                    body_display_name(body),
                    entity_token(body),
                )
                try:
                    manual_face = (
                        merge_manual_face
                        if merge_bodies
                        else _manual_face_for_body(manual_input, body)
                        if mode == FACE_MODES[1]
                        else None
                    )
                    analysis = analyse_body(
                        design=design,
                        body=body,
                        selection_index=selection_index,
                        face_selection_mode=(
                            "Manual: Merge Bodies reference face"
                            if merge_bodies else mode
                        ),
                        tolerance_internal=tolerance_internal,
                        manual_front_face=manual_face,
                        component_name_override=merged_component_name,
                        body_name_override=merged_component_name,
                        material_name_override=merged_material_name,
                    )
                    analyses.append(analysis)
                    logger.info(
                        "Analysis result=%s",
                        json.dumps(analysis.to_dict(), ensure_ascii=False),
                    )
                except Exception as body_error:
                    logger.exception(
                        "Body analysis failed for selection %d.", selection_index
                    )
                    analyses.append(
                        _failed_analysis(
                            design,
                            body,
                            selection_index,
                            mode,
                            body_error,
                            component_name_override=merged_component_name,
                            material_name_override=merged_material_name,
                        )
                    )

            review_result = ui.messageBox(
                (
                    f"{format_analysis_summary(analyses)}\n\n"
                    "Export confirmed cutting and enabled front machining now?"
                ),
                "Cutting DXF Exporter — Analysis Review",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.QuestionIconType,
            )
            if review_result != adsk.core.DialogResults.DialogYes:
                logger.info("Operator cancelled after analysis review.")
                outcome = "cancelled after review"
                return

            filename_template = command_inputs.itemById(FILENAME_INPUT_ID).value
            delete_temporary_sketches = command_inputs.itemById(
                DELETE_TEMP_INPUT_ID
            ).value
            open_output_folder = command_inputs.itemById(
                OPEN_FOLDER_INPUT_ID
            ).value
            include_front_machining = command_inputs.itemById(
                INCLUDE_FRONT_INPUT_ID
            ).value
            include_rear_machining = command_inputs.itemById(
                INCLUDE_REAR_INPUT_ID
            ).value
            one_dxf_per_body = command_inputs.itemById(
                ONE_DXF_PER_BODY_INPUT_ID
            ).value
            include_depth_in_layer_names = command_inputs.itemById(
                DEPTH_LAYERS_INPUT_ID
            ).value
            mitre_offset_internal = command_inputs.itemById(
                MITRE_OFFSET_INPUT_ID
            ).value
            rebate_offset_internal = command_inputs.itemById(
                REBATE_OFFSET_INPUT_ID
            ).value
            if merge_bodies:
                export_results = export_phase_four_bodies_combined(
                    design=design,
                    bodies=bodies,
                    analyses=analyses,
                    output_folder=output_folder,
                    filename_template=filename_template,
                    include_front_machining=include_front_machining,
                    include_rear_machining=include_rear_machining,
                    include_depth_in_layer_names=include_depth_in_layer_names,
                    mitre_offset_internal=mitre_offset_internal,
                    rebate_offset_internal=rebate_offset_internal,
                    delete_temporary_sketches=delete_temporary_sketches,
                    logger=logger,
                    merge_reference_face=merge_reference_face,
                    merge_reference_analysis=analyses[0],
                )
                for export_result in export_results:
                    logger.info(
                        "Merged export result=%s",
                        json.dumps(export_result.to_dict(), ensure_ascii=False),
                    )
            elif one_dxf_per_body:
                export_results = []
                for body, analysis in zip(bodies, analyses):
                    export_result = export_phase_three_body(
                        design=design,
                        body=body,
                        analysis=analysis,
                        output_folder=output_folder,
                        filename_template=filename_template,
                        include_front_machining=include_front_machining,
                        include_rear_machining=include_rear_machining,
                        include_depth_in_layer_names=include_depth_in_layer_names,
                        mitre_offset_internal=mitre_offset_internal,
                        rebate_offset_internal=rebate_offset_internal,
                        delete_temporary_sketches=delete_temporary_sketches,
                        logger=logger,
                    )
                    export_results.append(export_result)
                    logger.info(
                        "Export result=%s",
                        json.dumps(export_result.to_dict(), ensure_ascii=False),
                    )
            else:
                export_results = export_phase_four_bodies_combined(
                    design=design,
                    bodies=bodies,
                    analyses=analyses,
                    output_folder=output_folder,
                    filename_template=filename_template,
                    include_front_machining=include_front_machining,
                    include_rear_machining=include_rear_machining,
                    include_depth_in_layer_names=include_depth_in_layer_names,
                    mitre_offset_internal=mitre_offset_internal,
                    rebate_offset_internal=rebate_offset_internal,
                    delete_temporary_sketches=delete_temporary_sketches,
                    logger=logger,
                )
                for export_result in export_results:
                    logger.info(
                        "Combined export result=%s",
                        json.dumps(export_result.to_dict(), ensure_ascii=False),
                    )

            report_paths = []
            report_errors = []
            if command_inputs.itemById(WRITE_CSV_INPUT_ID).value:
                try:
                    report_paths.append(
                        write_analysis_csv(
                            output_folder,
                            analyses,
                            export_results,
                            include_depth_in_layer_names,
                        )
                    )
                except Exception as report_error:
                    logger.exception("CSV report writing failed.")
                    report_errors.append(f"CSV report: {report_error}")
            if command_inputs.itemById(WRITE_JSON_INPUT_ID).value:
                try:
                    report_paths.append(
                        write_analysis_json(
                            output_folder,
                            analyses,
                            export_results,
                            ADDIN_VERSION,
                            include_depth_in_layer_names,
                        )
                    )
                except Exception as report_error:
                    logger.exception("JSON report writing failed.")
                    report_errors.append(f"JSON report: {report_error}")

            ui.messageBox(
                _format_export_summary(
                    analyses,
                    export_results,
                    report_paths,
                    report_errors,
                ),
                "Cutting DXF Exporter — Export Results",
            )
            if open_output_folder and any(
                result.succeeded for result in export_results
            ):
                try:
                    os.startfile(output_folder)
                except OSError:
                    logger.exception("Could not open the output folder.")
            outcome = "completed"
        except Exception as error:
            logger.exception("Analysis command failed.")
            show_error(ui, "Cutting DXF Exporter", error)
        finally:
            if temporary_union:
                cleanup_error = temporary_union.cleanup()
                if cleanup_error:
                    logger.error(cleanup_error)
                    ui.messageBox(
                        cleanup_error,
                        "Cutting DXF Exporter — Cleanup Warning",
                    )
            finish_session(logger, outcome)


def _format_export_summary(
    analyses: List[BodyAnalysis],
    export_results: List[ExportResult],
    report_paths: List[str],
    report_errors: List[str],
) -> str:
    lines = ["CUTTING DXF EXPORTER — PHASE 4 RESULTS", ""]
    for analysis, result in zip(analyses, export_results):
        lines.append(f"{analysis.component_name} / {analysis.body_name}")
        if result.succeeded:
            lines.append(f"  Exported: {result.output_path}")
            lines.append(
                f"  Operations exported: {result.exported_operation_count}"
            )
        else:
            lines.append(f"  Failed: {result.error_message}")
            if result.backup_paths:
                lines.append("  Fusion DXF backups retained:")
                lines.extend(f"    - {path}" for path in result.backup_paths)
        lines.append(
            "  Temporary sketches: "
            f"{'cleaned' if result.temporary_sketches_cleaned else 'retained'}"
        )
        for warning in result.warnings:
            lines.append(f"  Warning: {warning.message}")
        lines.append("")
    succeeded = sum(1 for result in export_results if result.succeeded)
    lines.append(f"Completed: {succeeded} of {len(export_results)} bodies exported.")
    if report_paths:
        lines.append("")
        lines.append("Reports:")
        lines.extend(f"  {path}" for path in report_paths)
    if report_errors:
        lines.append("")
        lines.append("Report warnings:")
        lines.extend(f"  {error}" for error in report_errors)
    return "\n".join(lines)


def _toolbar_panel(
    ui: adsk.core.UserInterface,
) -> Optional[adsk.core.ToolbarPanel]:
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    return workspace.toolbarPanels.itemById(PANEL_ID) if workspace else None


def _command_control(
    ui: adsk.core.UserInterface,
) -> Optional[adsk.core.CommandControl]:
    panel = _toolbar_panel(ui)
    return panel.controls.itemById(COMMAND_ID) if panel else None


def _manual_faces_cover_bodies(
    manual_input: adsk.core.SelectionCommandInput,
    bodies: List[adsk.fusion.BRepBody],
) -> bool:
    return all(_manual_face_for_body(manual_input, body) is not None for body in bodies)


def _combined_reference_face(
    bodies: List[adsk.fusion.BRepBody],
    reference_face: adsk.fusion.BRepFace,
    combined_body: adsk.fusion.BRepBody,
    tolerance_internal: float,
) -> adsk.fusion.BRepFace:
    if len(bodies) != 2 or not reference_face:
        raise ValueError("Merge Bodies requires exactly two bodies and one face.")
    reference_body = next(
        (body for body in bodies if _face_belongs_to_body(reference_face, body)),
        None,
    )
    if not reference_body:
        raise ValueError("The merge reference face must belong to a selected body.")
    reference_record = record_for_manual_face(
        collect_planar_faces(reference_body),
        reference_face,
    )
    if not reference_record:
        raise ValueError("The merge reference face must be planar.")
    candidates = collect_coplanar_support_faces(
        reference_record,
        collect_planar_faces(combined_body),
        tolerance_internal,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    match = largest_planar_face(candidates)
    if not match:
        raise RuntimeError(
            "The selected reference plane was not found on the temporary "
            "combined body. Select an exposed front face and try again."
        )
    return match.face


def _merge_metadata(
    bodies: List[adsk.fusion.BRepBody],
) -> tuple:
    component_names = {
        body_component_name(body).strip()
        for body in bodies
        if body_component_name(body).strip()
    }
    component_name = (
        next(iter(component_names))
        if len(component_names) == 1
        else "Merged_Selected_Bodies"
    )
    material_names = {
        body_material_name(body).strip()
        for body in bodies
        if body_material_name(body).strip()
    }
    material_name = " + ".join(sorted(material_names)) or "Combined materials"
    return component_name, material_name


def _face_belongs_to_body(
    face: adsk.fusion.BRepFace,
    body: adsk.fusion.BRepBody,
) -> bool:
    if not face or not face.body:
        return False
    face_body = face.body
    face_token = entity_token(face_body)
    body_token = entity_token(body)
    if face_token and body_token:
        return face_token == body_token
    face_temp_id = temp_id(face_body)
    body_temp_id = temp_id(body)
    return face_temp_id is not None and face_temp_id == body_temp_id


def _manual_face_for_body(
    manual_input: adsk.core.SelectionCommandInput,
    body: adsk.fusion.BRepBody,
) -> Optional[adsk.fusion.BRepFace]:
    body_token = entity_token(body)
    body_temp_id = temp_id(body)
    for index in range(manual_input.selectionCount):
        face = adsk.fusion.BRepFace.cast(manual_input.selection(index).entity)
        if not face or not face.body:
            continue
        if body_token and entity_token(face.body) == body_token:
            return face
        if body_temp_id is not None and temp_id(face.body) == body_temp_id:
            return face
    return None


def _failed_analysis(
    design: adsk.fusion.Design,
    body: adsk.fusion.BRepBody,
    selection_index: int,
    mode: str,
    error: Exception,
    component_name_override: Optional[str] = None,
    material_name_override: Optional[str] = None,
):
    from ..models.analysis_models import (
        AnalysisWarning,
        BodyAnalysis,
        WarningSeverity,
    )
    from ..utilities.fusion_utils import (
        body_component_name,
        body_display_name,
        body_material_name,
        design_name,
    )

    return BodyAnalysis(
        design_name=design_name(design),
        component_name=component_name_override or body_component_name(body),
        body_name=component_name_override or body_display_name(body),
        body_token=body_identity(body, selection_index),
        selection_index=selection_index,
        valid_solid=False,
        face_selection_mode=mode,
        material_name=material_name_override or body_material_name(body),
        warnings=[
            AnalysisWarning(
                code="UNEXPECTED_ANALYSIS_ERROR",
                message=str(error),
                severity=WarningSeverity.ERROR,
                requires_review=True,
            )
        ],
        operator_review_required=True,
    )
