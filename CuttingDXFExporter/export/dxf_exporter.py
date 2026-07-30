"""Fusion sketch export and Phase 3 body-DXF orchestration."""

import logging
import os
from typing import Any, Dict, Optional, Tuple

from ..models.analysis_models import (
    AnalysisWarning,
    BodyAnalysis,
    ExportResult,
    OperationType,
    WarningSeverity,
)
from ..utilities.file_utils import (
    append_thickness_suffix,
    material_output_folder,
    render_body_filename,
    unique_path,
)
from .dxf_postprocessor import merge_category_dxfs
from .sketch_builder import (
    TemporarySketchSet,
    build_phase_three_sketches,
    cleanup_temporary_sketches,
)


def detect_dxf_sketch_export_support(design: Optional[Any]) -> Tuple[bool, str]:
    """Detect the preview sketch-DXF export factory without invoking it."""

    if not design:
        return False, "Open a Fusion Design to check export capability."
    export_manager = getattr(design, "exportManager", None)
    if not export_manager:
        return False, "The active design does not expose an ExportManager."
    factory = getattr(export_manager, "createDXFSketchExportOptions", None)
    if not callable(factory):
        return (
            False,
            "createDXFSketchExportOptions is not available in this Fusion build.",
        )
    if not callable(getattr(export_manager, "execute", None)):
        return False, "ExportManager.execute is not available in this Fusion build."
    return (
        True,
        "createDXFSketchExportOptions and ExportManager.execute were detected.",
    )


def export_phase_three_body(
    design: Any,
    body: Any,
    analysis: BodyAnalysis,
    output_folder: str,
    filename_template: str,
    include_front_machining: bool,
    include_depth_in_layer_names: bool,
    mitre_offset_internal: float,
    rebate_offset_internal: float,
    delete_temporary_sketches: bool,
    logger: logging.Logger,
) -> ExportResult:
    """Export supported operations for one body without affecting other bodies."""

    result = ExportResult(body_token=analysis.body_token)
    sketch_set: Optional[TemporarySketchSet] = None
    category_paths: Dict[str, str] = {}
    try:
        supported, message = detect_dxf_sketch_export_support(design)
        if not supported:
            raise RuntimeError(message)
        if not _analysis_is_exportable(analysis):
            raise RuntimeError(
                "Body failed the solid, manufacturing-face, or sheet-envelope checks."
            )

        filename = render_body_filename(
            filename_template,
            analysis.component_name,
            analysis.body_name,
        )
        if analysis.thickness_mm is None:
            raise RuntimeError("Material thickness is unavailable for filename.")
        filename = append_thickness_suffix(
            filename,
            analysis.thickness_mm,
        )
        material_folder = material_output_folder(
            output_folder,
            analysis.material_name,
        )
        os.makedirs(material_folder, exist_ok=True)
        output_path = os.path.join(material_folder, f"{filename}.dxf")
        result.output_path = output_path

        sketch_set = TemporarySketchSet()
        build_phase_three_sketches(
            design,
            body,
            analysis,
            include_front_machining,
            include_depth_in_layer_names,
            mitre_offset_internal=mitre_offset_internal,
            rebate_offset_internal=rebate_offset_internal,
            sketch_set=sketch_set,
        )
        export_manager = design.exportManager
        for layer_name, sketch in sketch_set.sketches.items():
            category_path = unique_path(
                os.path.join(
                    material_folder,
                    f"{filename}.__fusion_{layer_name}.dxf",
                )
            )
            _export_sketch(export_manager, sketch, category_path)
            category_paths[layer_name] = category_path
            logger.info(
                "Fusion category export layer=%s path=%s",
                layer_name,
                category_path,
            )

        merge_result = merge_category_dxfs(
            category_paths,
            output_path,
            markup_text=os.path.basename(output_path),
        )
        result.succeeded = True
        result.exported_operation_count = sum(
            len(operations)
            for operations in _exported_operations_by_layer(
                analysis,
                include_front_machining,
                include_depth_in_layer_names,
            ).values()
        )
        logger.info(
            "Final DXF path=%s entity_counts=%s",
            output_path,
            merge_result.entity_counts,
        )
        for category_path in category_paths.values():
            try:
                os.remove(category_path)
            except OSError as error:
                result.warnings.append(
                    AnalysisWarning(
                        code="CATEGORY_BACKUP_CLEANUP_FAILED",
                        message=f"Could not remove {category_path}: {error}",
                        severity=WarningSeverity.WARNING,
                        requires_review=False,
                    )
                )
    except Exception as error:
        result.error_message = str(error)
        result.backup_paths = [
            path for path in category_paths.values()
            if os.path.isfile(path)
        ]
        logger.exception(
            "Phase 3 DXF export failed component=%s body=%s",
            analysis.component_name,
            analysis.body_name,
        )
    finally:
        if sketch_set and delete_temporary_sketches:
            cleanup_errors = cleanup_temporary_sketches(sketch_set)
            result.temporary_sketches_cleaned = not cleanup_errors
            for cleanup_error in cleanup_errors:
                result.warnings.append(
                    AnalysisWarning(
                        code="TEMPORARY_SKETCH_CLEANUP_FAILED",
                        message=cleanup_error,
                        severity=WarningSeverity.WARNING,
                        requires_review=True,
                    )
                )
                logger.error(cleanup_error)
        elif sketch_set:
            result.temporary_sketches_cleaned = False
            logger.info("Temporary sketch deletion was disabled by the operator.")
    return result


def _exported_operations_by_layer(
    analysis: BodyAnalysis,
    include_front_machining: bool,
    include_depth_in_layer_names: bool,
) -> Dict[str, list]:
    from ..utilities.layer_utils import layer_name_for_operation

    grouped: Dict[str, list] = {}
    supported = {
        OperationType.CUT_OUTSIDE,
        OperationType.CUT_INSIDE,
        OperationType.MITRE,
    }
    if include_front_machining:
        supported.update({OperationType.FRONT_POCKET, OperationType.FRONT_REBATE})
    for operation in analysis.operations:
        if operation.operation_type not in supported:
            continue
        layer_name = layer_name_for_operation(
            operation.operation_type,
            operation.depth_mm,
            include_depth_in_layer_names,
            angle_degrees=operation.angle_degrees,
        )
        grouped.setdefault(layer_name, []).append(operation)
    return grouped


def _export_sketch(export_manager: Any, sketch: Any, path: str) -> None:
    options = export_manager.createDXFSketchExportOptions(path, sketch)
    if not options:
        raise RuntimeError(
            f"Fusion did not create DXF export options for {sketch.name}."
        )
    if not export_manager.execute(options):
        raise RuntimeError(f"Fusion failed to export temporary sketch {sketch.name}.")
    if not os.path.isfile(path) or os.path.getsize(path) < 1:
        raise RuntimeError(f"Fusion reported success but did not create {path}.")


def _analysis_is_exportable(analysis: BodyAnalysis) -> bool:
    return bool(
        analysis.valid_solid
        and analysis.front_face
        and analysis.rear_face
        and analysis.thickness_internal
        and analysis.constant_thickness
        and analysis.operation_count(OperationType.CUT_OUTSIDE) > 0
    )
