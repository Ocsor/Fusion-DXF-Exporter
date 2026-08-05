"""Conservative flat-body and manufacturing-face analysis."""

import math
from typing import Any, Optional

from .face_analyser import (
    collect_coplanar_support_faces,
    collect_mitre_edges,
    collect_planar_faces,
    collect_rear_support_faces,
    find_opposite_face,
    largest_planar_face,
    record_for_manual_face,
    to_manufacturing_face,
)
from .feature_classifier import (
    classify_front_face_loops,
    classify_front_support_faces,
    classify_rear_support_faces,
    create_mitre_guide_operations,
)
from ..models.analysis_models import (
    AnalysisWarning,
    BodyAnalysis,
    OperationType,
    WarningSeverity,
)
from ..utilities.fusion_utils import (
    body_component_name,
    body_display_name,
    body_identity,
    body_material_name,
    design_name,
)
from ..utilities.geometry_utils import (
    internal_length_to_mm,
    iter_fusion_collection,
    point_tuple,
    signed_plane_offset,
)

DEFAULT_ANGULAR_TOLERANCE_RADIANS = math.radians(0.5)


def analyse_body(
    design: Any,
    body: Any,
    selection_index: int,
    face_selection_mode: str,
    tolerance_internal: float,
    manual_front_face: Optional[Any] = None,
    component_name_override: Optional[str] = None,
    body_name_override: Optional[str] = None,
    material_name_override: Optional[str] = None,
) -> BodyAnalysis:
    """Analyse one selected body without changing the design."""

    analysis = BodyAnalysis(
        design_name=design_name(design),
        component_name=(
            component_name_override or body_component_name(body)
        ),
        body_name=(
            body_name_override or body_display_name(body)
        ),
        body_token=body_identity(body, selection_index),
        selection_index=selection_index,
        valid_solid=False,
        face_selection_mode=face_selection_mode,
        material_name=(material_name_override or body_material_name(body)),
    )
    if not _is_temporary_body(body) and not getattr(body, "isValid", True):
        _warn(
            analysis,
            "INVALID_BODY_REFERENCE",
            "The selected body reference is no longer valid.",
            WarningSeverity.ERROR,
            True,
        )
        return analysis
    if not bool(body.isSolid):
        _warn(
            analysis,
            "BODY_NOT_SOLID",
            "The selected B-Rep body is open or is a surface body.",
            WarningSeverity.ERROR,
            True,
        )
        return analysis
    analysis.valid_solid = True

    planar_faces = collect_planar_faces(body)
    analysis.planar_face_count = len(planar_faces)
    if not planar_faces:
        _warn(
            analysis,
            "NO_PLANAR_FACES",
            "No planar manufacturing face was found.",
            WarningSeverity.ERROR,
            True,
        )
        return analysis

    if manual_front_face:
        front = record_for_manual_face(planar_faces, manual_front_face)
        if not front:
            _warn(
                analysis,
                "MANUAL_FACE_NOT_ON_BODY",
                "The manual front face is not a planar face of this body.",
                WarningSeverity.ERROR,
                True,
            )
            return analysis
    else:
        front = largest_planar_face(planar_faces)
    if not front:
        _warn(
            analysis,
            "FRONT_FACE_NOT_FOUND",
            "A proposed front manufacturing face could not be selected.",
            WarningSeverity.ERROR,
            True,
        )
        return analysis

    rear = find_opposite_face(
        front,
        planar_faces,
        tolerance_internal,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    if not rear:
        analysis.front_face = to_manufacturing_face(front, "front")
        _warn(
            analysis,
            "OPPOSITE_FACE_NOT_FOUND",
            "No reliable opposed planar support face was found.",
            WarningSeverity.ERROR,
            True,
        )
        analysis.operations = classify_front_face_loops(
            front.face,
            [],
            front.origin,
            front.normal,
            0.0,
            0.0,
            tolerance_internal,
            DEFAULT_ANGULAR_TOLERANCE_RADIANS,
        )
        _apply_operation_review_state(analysis)
        return analysis

    orientation_switched = False
    if not manual_front_face:
        front, rear, orientation_switched = _choose_automatic_front_side(
            front,
            rear,
            planar_faces,
            tolerance_internal,
        )

    front_support_faces = collect_coplanar_support_faces(
        front,
        planar_faces,
        tolerance_internal,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    rear_support_faces = collect_rear_support_faces(
        front,
        rear,
        planar_faces,
        tolerance_internal,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    analysis.front_face = to_manufacturing_face(front, "front")
    analysis.rear_face = to_manufacturing_face(rear, "rear")
    if orientation_switched:
        _warn(
            analysis,
            "AUTOMATIC_FRONT_SIDE_SWITCHED",
            (
                "Automatic analysis selected the opposite support plane because "
                "it contains more confidently detected front machining."
            ),
            WarningSeverity.INFO,
            True,
        )
    rear_offset = signed_plane_offset(front.origin, rear.origin, front.normal)
    thickness_internal = abs(rear_offset)
    analysis.thickness_internal = thickness_internal
    analysis.thickness_mm = internal_length_to_mm(thickness_internal)
    analysis.constant_thickness = _body_fits_support_slab(
        body,
        front.origin,
        front.normal,
        thickness_internal,
        tolerance_internal,
    )
    if not analysis.constant_thickness:
        _warn(
            analysis,
            "SHEET_ENVELOPE_CHECK_FAILED",
            (
                "Body vertices do not remain between the proposed front and "
                "rear support planes within tolerance."
            ),
            WarningSeverity.WARNING,
            True,
        )

    if face_selection_mode == "Automatic with review":
        _warn(
            analysis,
            "AUTOMATIC_REVIEW_REQUESTED",
            "The operator requested review of the automatically selected face.",
            WarningSeverity.INFO,
            True,
        )

    outside_profile = max(
        front_support_faces + rear_support_faces,
        key=lambda record: record.area_internal,
    )
    analysis.operations = classify_front_support_faces(
        [record.face for record in front_support_faces],
        [record.face for record in rear_support_faces],
        outside_profile.face,
        front.origin,
        front.normal,
        thickness_internal,
        analysis.thickness_mm,
        tolerance_internal,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    analysis.operations.extend(
        classify_rear_support_faces(
            [record.face for record in rear_support_faces],
            [record.face for record in front_support_faces],
            outside_profile.face,
            rear.origin,
            rear.normal,
            thickness_internal,
            analysis.thickness_mm,
            tolerance_internal,
            DEFAULT_ANGULAR_TOLERANCE_RADIANS,
        )
    )
    mitre_edges = collect_mitre_edges(
        planar_faces,
        front_support_faces,
        rear_support_faces,
        outside_profile,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    analysis.operations.extend(create_mitre_guide_operations(mitre_edges))
    analysis.feature_analysis_complete = True
    _apply_operation_review_state(analysis)
    _warn(
        analysis,
        "PHASE4_CLASSIFICATION_LIMIT",
        (
            "Phase 4 classifies simple front and rear pockets, edge rebates, "
            "and planar full-thickness mitres; complex features remain "
            "unsupported."
        ),
        WarningSeverity.INFO,
        True,
    )
    return analysis


def _is_temporary_body(body: Any) -> bool:
    for property_name in ("isTemporary", "isTransient"):
        try:
            if bool(getattr(body, property_name)):
                return True
        except Exception:
            continue
    return False


def _choose_automatic_front_side(
    proposed_front: Any,
    proposed_rear: Any,
    planar_faces: Any,
    tolerance_internal: float,
):
    """Prefer the support side with stronger front-machining evidence."""

    proposed_score = _front_machining_score(
        proposed_front,
        proposed_rear,
        planar_faces,
        tolerance_internal,
    )
    opposite_score = _front_machining_score(
        proposed_rear,
        proposed_front,
        planar_faces,
        tolerance_internal,
    )
    if opposite_score > proposed_score:
        return proposed_rear, proposed_front, True
    return proposed_front, proposed_rear, False


def _front_machining_score(
    front: Any,
    rear: Any,
    planar_faces: Any,
    tolerance_internal: float,
) -> int:
    front_support_faces = collect_coplanar_support_faces(
        front,
        planar_faces,
        tolerance_internal,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    rear_support_faces = collect_rear_support_faces(
        front,
        rear,
        planar_faces,
        tolerance_internal,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    thickness_internal = abs(
        signed_plane_offset(front.origin, rear.origin, front.normal)
    )
    operations = classify_front_support_faces(
        [record.face for record in front_support_faces],
        [record.face for record in rear_support_faces],
        front.face,
        front.origin,
        front.normal,
        thickness_internal,
        internal_length_to_mm(thickness_internal),
        tolerance_internal,
        DEFAULT_ANGULAR_TOLERANCE_RADIANS,
    )
    return sum(
        1
        for operation in operations
        if operation.operation_type
        in {OperationType.FRONT_POCKET, OperationType.FRONT_REBATE}
    )


def _body_fits_support_slab(
    body: Any,
    front_origin: Any,
    front_normal: Any,
    thickness_internal: float,
    tolerance_internal: float,
) -> bool:
    """Check that every body vertex lies between the two support planes."""

    sample_points = [
        point_tuple(vertex.geometry)
        for vertex in iter_fusion_collection(body.vertices)
    ]
    sample_points.extend(
        point_tuple(face.pointOnFace)
        for face in iter_fusion_collection(body.faces)
    )
    offsets = [
        signed_plane_offset(front_origin, point, front_normal)
        for point in sample_points
    ]
    if not offsets:
        return False
    maximum = max(offsets)
    minimum = min(offsets)
    return (
        maximum <= tolerance_internal
        and maximum >= -tolerance_internal
        and minimum >= -(thickness_internal + tolerance_internal)
        and abs(minimum + thickness_internal) <= tolerance_internal
    )


def _apply_operation_review_state(analysis: BodyAnalysis) -> None:
    if any(
        operation.operation_type == OperationType.UNKNOWN
        or operation.operator_approval_required
        for operation in analysis.operations
    ):
        analysis.operator_review_required = True


def _warn(
    analysis: BodyAnalysis,
    code: str,
    message: str,
    severity: WarningSeverity,
    requires_review: bool,
) -> None:
    analysis.warnings.append(
        AnalysisWarning(
            code=code,
            message=message,
            severity=severity,
            requires_review=requires_review,
        )
    )
    if requires_review:
        analysis.operator_review_required = True
