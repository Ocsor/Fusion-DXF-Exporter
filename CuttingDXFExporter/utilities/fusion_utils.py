"""Fusion-specific utility functions kept outside command and analysis logic."""

import traceback
from typing import Any, Dict, Iterable, List, Optional, Tuple

import adsk.core
import adsk.fusion

from ..models.analysis_models import BodyAnalysis, OperationType
from .geometry_utils import iter_fusion_collection


def active_design() -> Optional[adsk.fusion.Design]:
    """Return the active Fusion design, or None for non-design products."""

    application = adsk.core.Application.get()
    return adsk.fusion.Design.cast(application.activeProduct) if application else None


def entity_token(entity: Any) -> str:
    """Return the current document-lifetime token when available."""

    try:
        return str(entity.entityToken or "")
    except Exception:
        return ""


def temp_id(entity: Any) -> Optional[int]:
    """Return a temporary B-Rep identifier when available."""

    try:
        return int(entity.tempId)
    except Exception:
        return None


def body_identity(body: adsk.fusion.BRepBody, selection_index: int) -> str:
    """Return a stable in-session key without hashing a Fusion wrapper."""

    token = entity_token(body)
    if token:
        return token
    component_name = body_component_name(body)
    return f"{component_name}|{body_display_name(body)}|selection-{selection_index}"


def body_display_name(body: adsk.fusion.BRepBody) -> str:
    """Return a safe body name for persistent or temporary B-Rep bodies."""

    try:
        name = str(body.name or "").strip()
        if name:
            return name
    except Exception:
        pass
    return "Temporary Combined Body"


class TemporaryUnionBody:
    """Persisted union body and the exact feature used to remove it."""

    def __init__(
        self,
        body: adsk.fusion.BRepBody,
        base_feature: Optional[Any] = None,
    ) -> None:
        self.body = body
        self.base_feature = base_feature

    def cleanup(self) -> Optional[str]:
        try:
            if self.base_feature and getattr(self.base_feature, "isValid", False):
                if not self.base_feature.deleteMe():
                    return "Fusion did not delete the temporary union Base Feature."
                return None
            if self.body and getattr(self.body, "isValid", False):
                if not self.body.deleteMe():
                    return "Fusion did not delete the temporary union body."
        except Exception as error:
            return f"Could not delete the temporary union body: {error}"
        return None


def temporary_union_body(
    design: adsk.fusion.Design,
    bodies: Iterable[adsk.fusion.BRepBody],
) -> TemporaryUnionBody:
    """Create a disposable persisted union while preserving the source bodies."""

    source_bodies = list(bodies)
    if len(source_bodies) != 2:
        raise ValueError("Temporary body union requires exactly two bodies.")
    manager = adsk.fusion.TemporaryBRepManager.get()
    if not manager:
        raise RuntimeError("Fusion did not provide a TemporaryBRepManager.")
    target_body = manager.copy(source_bodies[0])
    tool_body = manager.copy(source_bodies[1])
    if not target_body or not tool_body:
        raise RuntimeError("Fusion could not copy the selected bodies for merging.")
    if not manager.booleanOperation(
        target_body,
        tool_body,
        adsk.fusion.BooleanTypes.UnionBooleanType,
    ):
        raise RuntimeError(
            "Fusion could not combine the copied bodies. Ensure the selected "
            "bodies touch or overlap without a gap."
        )
    if not target_body.isSolid:
        raise RuntimeError("The temporary combined body is not a closed solid.")
    if target_body.lumps.count != 1:
        raise RuntimeError(
            "The copied bodies did not form one connected solid. Ensure they "
            "touch or overlap without a gap."
        )
    root_component = design.rootComponent
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        base_feature = root_component.features.baseFeatures.add()
        if not base_feature:
            raise RuntimeError("Fusion could not create a temporary Base Feature.")
        base_feature.name = "DXF_TEMP_MERGED_BODY"
        try:
            if not base_feature.startEdit():
                raise RuntimeError("Fusion could not edit the temporary Base Feature.")
            source_body = root_component.bRepBodies.add(target_body, base_feature)
            if not source_body:
                raise RuntimeError("Fusion could not persist the temporary union body.")
            if not base_feature.finishEdit():
                raise RuntimeError("Fusion could not finish the temporary Base Feature.")
            if base_feature.bodies.count < 1:
                raise RuntimeError("The temporary Base Feature has no result body.")
            persisted_body = base_feature.bodies.item(0)
            _prepare_temporary_union_body(persisted_body)
            return TemporaryUnionBody(persisted_body, base_feature)
        except Exception:
            try:
                base_feature.finishEdit()
            except Exception:
                pass
            try:
                if getattr(base_feature, "isValid", False):
                    base_feature.deleteMe()
            except Exception:
                pass
            raise

    persisted_body = root_component.bRepBodies.add(target_body)
    if not persisted_body:
        raise RuntimeError("Fusion could not persist the temporary union body.")
    _prepare_temporary_union_body(persisted_body)
    return TemporaryUnionBody(persisted_body)


def _prepare_temporary_union_body(body: adsk.fusion.BRepBody) -> None:
    body.name = "DXF_TEMP_MERGED_BODY"
    body.isLightBulbOn = False


def body_component_name(body: adsk.fusion.BRepBody) -> str:
    """Return the owning component or occurrence name for a body."""

    try:
        occurrence = body.assemblyContext
        if occurrence:
            occurrence_name = str(occurrence.name or "").strip()
            if occurrence_name:
                return occurrence_name
            return str(occurrence.component.name)
    except Exception:
        pass
    try:
        return str(body.parentComponent.name)
    except Exception:
        return "Unknown Component"


def body_material_name(body: adsk.fusion.BRepBody) -> str:
    """Return the body's effective physical-material display name."""

    try:
        material = body.material
        if material and str(material.name).strip():
            return str(material.name).strip()
    except Exception:
        pass
    try:
        component = body.parentComponent
        material = component.material if component else None
        if material and str(material.name).strip():
            return str(material.name).strip()
    except Exception:
        pass
    return "Unspecified Material"


def design_name(design: adsk.fusion.Design) -> str:
    """Return the current document name."""

    try:
        return str(design.parentDocument.name)
    except Exception:
        return "Untitled Design"


def selected_bodies(
    selection_input: adsk.core.SelectionCommandInput,
) -> List[adsk.fusion.BRepBody]:
    """Return valid BRepBody selections without assuming list semantics."""

    bodies: List[adsk.fusion.BRepBody] = []
    for index in range(selection_input.selectionCount):
        body = adsk.fusion.BRepBody.cast(selection_input.selection(index).entity)
        if body:
            bodies.append(body)
    return bodies


def selected_faces_by_body(
    selection_input: adsk.core.SelectionCommandInput,
) -> Dict[str, adsk.fusion.BRepFace]:
    """Index manually selected planar faces by their parent body token."""

    selected: Dict[str, adsk.fusion.BRepFace] = {}
    for index in range(selection_input.selectionCount):
        face = adsk.fusion.BRepFace.cast(selection_input.selection(index).entity)
        if not face:
            continue
        body = face.body
        if body:
            selected[entity_token(body)] = face
    return selected


def format_analysis_summary(analyses: Iterable[BodyAnalysis]) -> str:
    """Build the operator-facing Phase 4 review."""

    sections: List[str] = [
        "CUTTING DXF EXPORTER — PHASE 4 ANALYSIS",
        "Review all classifications before approving DXF export.",
        "",
    ]
    for analysis in analyses:
        thickness = (
            f"{analysis.thickness_mm:.3f} mm"
            if analysis.thickness_mm is not None
            else "Not determined"
        )
        front_face = (
            f"face {analysis.front_face.face_index} "
            f"({analysis.front_face.area_mm2:.2f} mm²)"
            if analysis.front_face else "Not determined"
        )
        rear_face = (
            f"face {analysis.rear_face.face_index}"
            if analysis.rear_face else "Not determined"
        )
        sections.extend(
            [
                f"{analysis.component_name} / {analysis.body_name}",
                f"  Physical material: {analysis.material_name}",
                f"  Valid solid: {'Yes' if analysis.valid_solid else 'No'}",
                f"  Planar faces: {analysis.planar_face_count}",
                f"  Proposed front: {front_face}",
                f"  Opposite face: {rear_face}",
                f"  Nominal thickness: {thickness}",
                (
                    "  Sheet envelope check: "
                    f"{'Passed' if analysis.constant_thickness else 'Review required'}"
                ),
                (
                    "  Outside profiles: "
                    f"{analysis.operation_count(OperationType.CUT_OUTSIDE)}"
                ),
                (
                    "  Confirmed through-cuts: "
                    f"{analysis.operation_count(OperationType.CUT_INSIDE)}"
                ),
                (
                    "  Front pockets: "
                    f"{_operation_depth_summary(analysis, OperationType.FRONT_POCKET)}"
                ),
                (
                    "  Front rebates: "
                    f"{_operation_depth_summary(analysis, OperationType.FRONT_REBATE)}"
                ),
                (
                    "  Rear pockets: "
                    f"{_operation_depth_summary(analysis, OperationType.BACK_POCKET)}"
                ),
                (
                    "  Rear rebates: "
                    f"{_operation_depth_summary(analysis, OperationType.BACK_REBATE)}"
                ),
                (
                    "  Mitre guides: "
                    f"{analysis.operation_count(OperationType.MITRE)}"
                ),
                (
                    "  Unresolved visible boundaries: "
                    f"{analysis.operation_count(OperationType.UNKNOWN)}"
                ),
                (
                    "  Operator review: "
                    f"{'Required' if analysis.operator_review_required else 'Not required'}"
                ),
            ]
        )
        if analysis.warnings:
            sections.append("  Warnings:")
            sections.extend(
                f"    - [{warning.code}] {warning.message}"
                for warning in analysis.warnings
            )
        operation_warnings = [
            warning
            for operation in analysis.operations
            for warning in operation.warnings
        ]
        if operation_warnings:
            sections.append("  Geometry warnings:")
            sections.extend(
                f"    - [{warning.code}] {warning.message}"
                for warning in operation_warnings
            )
        sections.append("")
    sections.extend(
        [
            "UNKNOWN boundaries will not be exported.",
            "Choose Yes only after reviewing every body and warning.",
        ]
    )
    return "\n".join(sections)


def _operation_depth_summary(
    analysis: BodyAnalysis,
    operation_type: OperationType,
) -> str:
    depths = [
        operation.depth_mm
        for operation in analysis.operations
        if operation.operation_type == operation_type
        and operation.depth_mm is not None
    ]
    if not depths:
        return "0"
    formatted = ", ".join(f"{depth:.3f} mm" for depth in depths)
    return f"{len(depths)} ({formatted})"


def show_error(ui: Optional[adsk.core.UserInterface], title: str, error: Exception) -> None:
    """Show a concise Fusion message while retaining traceback text for logs."""

    if ui:
        ui.messageBox(f"{error}\n\nSee cutting_dxf_export.log for details.", title)


def traceback_text() -> str:
    """Return the active exception traceback."""

    return traceback.format_exc()
