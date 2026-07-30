"""Conservative cutting, pocket, and rebate classification."""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import adsk.core

from ..models.analysis_models import (
    AnalysisWarning,
    ConfidenceLevel,
    DetectedOperation,
    GeometryLoop,
    OperationSide,
    OperationType,
    WarningSeverity,
)
from ..utilities.fusion_utils import entity_token, temp_id
from ..utilities.geometry_utils import (
    are_parallel,
    dot,
    internal_length_to_mm,
    iter_fusion_collection,
    normalize,
    point_tuple,
    signed_plane_offset,
)
from ..utilities.layer_utils import layer_name_for_operation


def classify_front_face_loops(
    front_face: Any,
    rear_faces: Iterable[Any],
    front_origin: Tuple[float, float, float],
    front_normal: Tuple[float, float, float],
    thickness_internal: float,
    thickness_mm: float,
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
) -> List[DetectedOperation]:
    """Classify supported operations visible from the front manufacturing face."""

    operations: List[DetectedOperation] = []
    pocket_floor_keys = set()
    rear_face_list = list(rear_faces or [])
    for loop_index, loop in enumerate(iter_fusion_collection(front_face.loops)):
        boundary = _geometry_loop(loop, loop_index)
        wall_faces, wall_error = _wall_faces_for_loop(loop, front_face)
        if loop.isOuter:
            operations.append(
                _operation(
                    OperationType.CUT_OUTSIDE,
                    OperationSide.THROUGH,
                    boundary,
                )
            )
            operations.extend(
                _detect_front_rebates(
                    wall_faces,
                    front_face,
                    front_origin,
                    front_normal,
                    thickness_internal,
                    distance_tolerance_internal,
                    angular_tolerance_radians,
                    pocket_floor_keys,
                )
            )
            continue

        if not wall_error and _walls_touch_rear(wall_faces, rear_face_list):
            operations.append(
                _operation(
                    OperationType.CUT_INSIDE,
                    OperationSide.THROUGH,
                    boundary,
                    depth_internal=thickness_internal,
                    depth_mm=thickness_mm,
                )
            )
            continue

        pocket, pocket_floor, pocket_reason = _classify_front_pocket(
            wall_faces,
            wall_error,
            front_face,
            front_origin,
            front_normal,
            thickness_internal,
            distance_tolerance_internal,
            angular_tolerance_radians,
            boundary,
        )
        if pocket:
            operations.append(pocket)
            pocket_floor_keys.add(_entity_key(pocket_floor))
            operations.extend(
                _classify_nested_floor_loops(
                    pocket_floor,
                    rear_face_list,
                    thickness_internal,
                    thickness_mm,
                )
            )
            continue

        reason = pocket_reason or wall_error
        if not reason:
            reason = (
                "The opening does not terminate unambiguously at the rear "
                "support plane or one supported planar pocket floor."
            )
        operations.append(_unknown_operation(boundary, reason))
    return _deduplicate_operations(operations)


def classify_front_support_faces(
    front_faces: Iterable[Any],
    rear_faces: Iterable[Any],
    outside_profile_face: Any,
    front_origin: Tuple[float, float, float],
    front_normal: Tuple[float, float, float],
    thickness_internal: float,
    thickness_mm: float,
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
) -> List[DetectedOperation]:
    """Classify every coplanar front-face fragment as one manufacturing side."""

    rear_face_list = list(rear_faces)
    operations: List[DetectedOperation] = []
    for front_face in front_faces:
        operations.extend(
            operation
            for operation in classify_front_face_loops(
                front_face,
                rear_face_list,
                front_origin,
                front_normal,
                thickness_internal,
                thickness_mm,
                distance_tolerance_internal,
                angular_tolerance_radians,
            )
            if operation.operation_type != OperationType.CUT_OUTSIDE
        )
    operations.extend(_outside_profile_operations(outside_profile_face))
    return _deduplicate_operations(operations)


def classify_rear_support_faces(
    rear_faces: Iterable[Any],
    front_faces: Iterable[Any],
    outside_profile_face: Any,
    rear_origin: Tuple[float, float, float],
    rear_normal: Tuple[float, float, float],
    thickness_internal: float,
    thickness_mm: float,
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
) -> List[DetectedOperation]:
    """Classify machining visible from the rear manufacturing face."""

    candidates = classify_front_support_faces(
        rear_faces,
        front_faces,
        outside_profile_face,
        rear_origin,
        rear_normal,
        thickness_internal,
        thickness_mm,
        distance_tolerance_internal,
        angular_tolerance_radians,
    )
    operation_types = {
        OperationType.FRONT_POCKET: OperationType.BACK_POCKET,
        OperationType.FRONT_REBATE: OperationType.BACK_REBATE,
    }
    operations = []
    for operation in candidates:
        operation_type = operation_types.get(operation.operation_type)
        if operation_type is not None:
            operation.operation_type = operation_type
            operation.side = OperationSide.BACK
            operation.proposed_layer = layer_name_for_operation(
                operation_type,
                operation.depth_mm,
                include_depth=True,
            )
            operations.append(operation)
            continue
        if operation.operation_type == OperationType.UNKNOWN:
            operation.side = OperationSide.BACK
            for warning in operation.warnings:
                if warning.code == "FRONT_FEATURE_NOT_CLASSIFIED":
                    warning.code = "REAR_FEATURE_NOT_CLASSIFIED"
            operations.append(operation)
    return _deduplicate_operations(operations)


def create_mitre_guide_operations(
    mitre_edges: Iterable[Any],
) -> List[DetectedOperation]:
    """Create one generated MITRE guide operation per straight source edge."""

    operations = []
    for edge_index, mitre_edge in enumerate(mitre_edges):
        edge = mitre_edge.edge
        boundary = _geometry_loop_from_edges([edge], edge_index, False)
        operations.append(
            _operation(
                OperationType.MITRE,
                OperationSide.FRONT,
                boundary,
                angle_degrees=mitre_edge.angle_degrees,
            )
        )
    return operations


def _outside_profile_operations(face: Any) -> List[DetectedOperation]:
    operations = []
    for loop_index, loop in enumerate(iter_fusion_collection(face.loops)):
        if not loop.isOuter:
            continue
        operations.append(
            _operation(
                OperationType.CUT_OUTSIDE,
                OperationSide.THROUGH,
                _geometry_loop(loop, loop_index),
            )
        )
    return operations


def _classify_nested_floor_loops(
    floor_face: Any,
    rear_faces: Iterable[Any],
    thickness_internal: float,
    thickness_mm: float,
) -> List[DetectedOperation]:
    """Classify bores that begin at an already confirmed pocket floor."""

    operations = []
    rear_face_list = list(rear_faces)
    for loop_index, loop in enumerate(iter_fusion_collection(floor_face.loops)):
        if loop.isOuter:
            continue
        boundary = _geometry_loop(loop, loop_index)
        wall_faces, wall_error = _wall_faces_for_loop(loop, floor_face)
        if not wall_error and _walls_touch_rear(wall_faces, rear_face_list):
            operations.append(
                _operation(
                    OperationType.CUT_INSIDE,
                    OperationSide.THROUGH,
                    boundary,
                    depth_internal=thickness_internal,
                    depth_mm=thickness_mm,
                    source_faces=[floor_face],
                )
            )
            continue
        reason = wall_error or (
            "A nested pocket-floor boundary does not terminate "
            "unambiguously at the rear support plane."
        )
        operations.append(
            _unknown_operation(
                boundary,
                reason,
                source_faces=[floor_face],
            )
        )
    return operations


def _classify_front_pocket(
    wall_faces: Iterable[Any],
    wall_error: str,
    front_face: Any,
    front_origin: Tuple[float, float, float],
    front_normal: Tuple[float, float, float],
    thickness_internal: float,
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
    boundary: GeometryLoop,
) -> Tuple[Optional[DetectedOperation], Optional[Any], str]:
    if wall_error:
        return None, None, wall_error

    walls = list(wall_faces)
    if not walls:
        return None, None, "No sidewalls were found for the enclosed boundary."
    for wall_face in walls:
        if not _is_supported_vertical_wall(
            wall_face,
            front_normal,
            angular_tolerance_radians,
        ):
            return (
                None,
                None,
                "A pocket sidewall is angled or has an unsupported surface type.",
            )

    common_floors: Optional[Dict[str, Tuple[Any, float]]] = None
    for wall_face in walls:
        candidates = _candidate_recess_floors(
            wall_face,
            front_face,
            front_origin,
            front_normal,
            thickness_internal,
            distance_tolerance_internal,
            angular_tolerance_radians,
        )
        if not candidates:
            return (
                None,
                None,
                "A single planar pocket floor was not found for every sidewall.",
            )
        if common_floors is None:
            common_floors = candidates
        else:
            common_floors = {
                key: value
                for key, value in common_floors.items()
                if key in candidates
            }

    if not common_floors or len(common_floors) != 1:
        return (
            None,
            None,
            "Multiple possible pocket floors were found for the boundary.",
        )
    floor_face, depth_internal = next(iter(common_floors.values()))
    depth_mm = internal_length_to_mm(depth_internal)
    operation = _operation(
        OperationType.FRONT_POCKET,
        OperationSide.FRONT,
        boundary,
        depth_internal=depth_internal,
        depth_mm=depth_mm,
        source_faces=[floor_face],
    )
    return operation, floor_face, ""


def _detect_front_rebates(
    wall_faces: Iterable[Any],
    front_face: Any,
    front_origin: Tuple[float, float, float],
    front_normal: Tuple[float, float, float],
    thickness_internal: float,
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
    excluded_floor_keys: set,
) -> List[DetectedOperation]:
    operations: List[DetectedOperation] = []
    seen_floor_keys = set(excluded_floor_keys)
    for wall_face in wall_faces:
        candidates = _candidate_recess_floors(
            wall_face,
            front_face,
            front_origin,
            front_normal,
            thickness_internal,
            distance_tolerance_internal,
            angular_tolerance_radians,
        )
        for floor_key, (floor_face, depth_internal) in candidates.items():
            if floor_key in seen_floor_keys:
                continue
            seen_floor_keys.add(floor_key)
            floor_loop = _single_outer_loop(floor_face)
            if not floor_loop:
                continue
            boundary = _geometry_loop(floor_loop[1], floor_loop[0])
            if not _is_supported_vertical_wall(
                wall_face,
                front_normal,
                angular_tolerance_radians,
            ):
                operations.append(
                    _unknown_operation(
                        boundary,
                        "A possible front rebate has an angled or unsupported wall.",
                        source_faces=[floor_face],
                    )
                )
                continue
            depth_mm = internal_length_to_mm(depth_internal)
            operations.append(
                _operation(
                    OperationType.FRONT_REBATE,
                    OperationSide.FRONT,
                    boundary,
                    depth_internal=depth_internal,
                    depth_mm=depth_mm,
                    source_faces=[floor_face],
                )
            )
    return operations


def _wall_faces_for_loop(
    loop: Any,
    front_face: Any,
) -> Tuple[List[Any], str]:
    wall_faces: Dict[str, Any] = {}
    for edge in iter_fusion_collection(loop.edges):
        adjacent_walls = [
            face
            for face in iter_fusion_collection(edge.faces)
            if not _same_brep_entity(face, front_face)
        ]
        if not adjacent_walls:
            return [], "A boundary edge has no adjacent wall face."
        for wall_face in adjacent_walls:
            wall_faces[_entity_key(wall_face)] = wall_face
    if not wall_faces:
        return [], "No wall faces were found for the boundary."
    return list(wall_faces.values()), ""


def _walls_touch_rear(
    wall_faces: Iterable[Any],
    rear_faces: Iterable[Any],
) -> bool:
    rear_face_list = list(rear_faces)
    walls = list(wall_faces)
    return bool(
        walls
        and rear_face_list
        and all(_face_touches_any_face(wall, rear_face_list) for wall in walls)
    )


def _candidate_recess_floors(
    wall_face: Any,
    front_face: Any,
    front_origin: Tuple[float, float, float],
    front_normal: Tuple[float, float, float],
    thickness_internal: float,
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
) -> Dict[str, Tuple[Any, float]]:
    candidates: Dict[str, Tuple[Any, float]] = {}
    for edge in iter_fusion_collection(wall_face.edges):
        for adjacent_face in iter_fusion_collection(edge.faces):
            if (
                _same_brep_entity(adjacent_face, wall_face)
                or _same_brep_entity(adjacent_face, front_face)
            ):
                continue
            depth = _supported_recess_depth(
                adjacent_face,
                front_origin,
                front_normal,
                thickness_internal,
                distance_tolerance_internal,
                angular_tolerance_radians,
            )
            if depth is not None:
                candidates[_entity_key(adjacent_face)] = (adjacent_face, depth)
    return candidates


def _supported_recess_depth(
    face: Any,
    front_origin: Tuple[float, float, float],
    front_normal: Tuple[float, float, float],
    thickness_internal: float,
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
) -> Optional[float]:
    if not adsk.core.Plane.cast(face.geometry):
        return None
    face_normal = _face_normal(face)
    if not face_normal or not are_parallel(
        front_normal,
        face_normal,
        angular_tolerance_radians,
    ):
        return None
    cosine_limit = math.cos(angular_tolerance_radians)
    if dot(normalize(front_normal), normalize(face_normal)) < cosine_limit:
        return None
    offset = signed_plane_offset(
        front_origin,
        point_tuple(face.pointOnFace),
        front_normal,
    )
    depth = -offset
    if depth <= distance_tolerance_internal:
        return None
    if depth >= thickness_internal - distance_tolerance_internal:
        return None
    return depth


def _is_supported_vertical_wall(
    face: Any,
    front_normal: Tuple[float, float, float],
    angular_tolerance_radians: float,
) -> bool:
    plane = adsk.core.Plane.cast(face.geometry)
    if plane:
        wall_normal = _face_normal(face)
        if not wall_normal:
            return False
        sine_limit = math.sin(angular_tolerance_radians)
        return abs(dot(normalize(front_normal), normalize(wall_normal))) <= sine_limit

    cylinder = adsk.core.Cylinder.cast(face.geometry)
    if cylinder:
        return are_parallel(
            front_normal,
            point_tuple(cylinder.axis),
            angular_tolerance_radians,
        )
    return False


def _face_normal(face: Any) -> Optional[Tuple[float, float, float]]:
    success, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
    return point_tuple(normal) if success and normal else None


def _face_touches_any_face(face: Any, target_faces: Iterable[Any]) -> bool:
    for edge in iter_fusion_collection(face.edges):
        for adjacent_face in iter_fusion_collection(edge.faces):
            if any(
                _same_brep_entity(adjacent_face, target_face)
                for target_face in target_faces
            ):
                return True
    return False


def _single_outer_loop(face: Any) -> Optional[Tuple[int, Any]]:
    outer_loops = [
        (index, loop)
        for index, loop in enumerate(iter_fusion_collection(face.loops))
        if loop.isOuter
    ]
    return outer_loops[0] if len(outer_loops) == 1 else None


def _operation(
    operation_type: OperationType,
    side: OperationSide,
    boundary: GeometryLoop,
    depth_internal: Optional[float] = None,
    depth_mm: Optional[float] = None,
    source_faces: Optional[Iterable[Any]] = None,
    angle_degrees: Optional[float] = None,
) -> DetectedOperation:
    faces = list(source_faces or [])
    return DetectedOperation(
        operation_type=operation_type,
        side=side,
        depth_internal=depth_internal,
        depth_mm=depth_mm,
        proposed_layer=layer_name_for_operation(
            operation_type,
            depth_mm,
            include_depth=True,
            angle_degrees=angle_degrees,
        ),
        angle_degrees=angle_degrees,
        source_face_tokens=[
            token for token in (entity_token(face) for face in faces) if token
        ],
        source_face_temp_ids=[
            face_id for face_id in (temp_id(face) for face in faces)
            if face_id is not None
        ],
        source_edge_tokens=boundary.edge_tokens,
        source_edge_temp_ids=boundary.edge_temp_ids,
        confidence=ConfidenceLevel.HIGH,
        is_closed=boundary.is_closed,
        geometry_summary=_geometry_summary(boundary),
        operator_approval_required=False,
        boundary=boundary,
    )


def _unknown_operation(
    boundary: GeometryLoop,
    reason: str,
    source_faces: Optional[Iterable[Any]] = None,
) -> DetectedOperation:
    operation = _operation(
        OperationType.UNKNOWN,
        OperationSide.FRONT,
        boundary,
        source_faces=source_faces,
    )
    operation.confidence = ConfidenceLevel.UNKNOWN
    operation.operator_approval_required = True
    operation.warnings.append(
        AnalysisWarning(
            code="FRONT_FEATURE_NOT_CLASSIFIED",
            message=reason,
            severity=WarningSeverity.WARNING,
            requires_review=True,
        )
    )
    return operation


def _deduplicate_operations(
    operations: Iterable[DetectedOperation],
) -> List[DetectedOperation]:
    deduplicated = []
    seen = set()
    for operation in operations:
        boundary_key = (
            tuple(
                f"id:{edge_id}"
                for edge_id in sorted(operation.source_edge_temp_ids)
            )
            or tuple(
                f"token:{token}"
                for token in sorted(operation.source_edge_tokens)
            )
        )
        key = (operation.operation_type.value, boundary_key)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(operation)
    return deduplicated


def _same_brep_entity(first: Any, second: Any) -> bool:
    if first is second:
        return True
    first_temp_id = temp_id(first)
    second_temp_id = temp_id(second)
    if first_temp_id is not None and first_temp_id == second_temp_id:
        return True
    first_token = entity_token(first)
    second_token = entity_token(second)
    return bool(first_token and first_token == second_token)


def _entity_key(entity: Any) -> str:
    entity_temp_id = temp_id(entity)
    if entity_temp_id is not None:
        return f"temp:{entity_temp_id}"
    token = entity_token(entity)
    return f"token:{token}" if token else f"object:{id(entity)}"


def _geometry_loop(loop: Any, loop_index: int) -> GeometryLoop:
    return _geometry_loop_from_edges(
        iter_fusion_collection(loop.edges),
        loop_index,
        bool(loop.isOuter),
    )


def _geometry_loop_from_edges(
    edges: Iterable[Any],
    loop_index: int,
    is_outer: bool,
) -> GeometryLoop:
    edge_tokens = []
    edge_temp_ids = []
    geometry_types = []
    for edge in edges:
        token = entity_token(edge)
        if token:
            edge_tokens.append(token)
        edge_id = temp_id(edge)
        if edge_id is not None:
            edge_temp_ids.append(edge_id)
        geometry = edge.geometry
        geometry_types.append(
            str(getattr(geometry, "objectType", type(geometry).__name__))
        )
    return GeometryLoop(
        loop_index=loop_index,
        is_outer=is_outer,
        edge_tokens=edge_tokens,
        edge_temp_ids=edge_temp_ids,
        geometry_types=geometry_types,
        is_closed=True,
    )


def _geometry_summary(boundary: GeometryLoop) -> str:
    unique_types = sorted(set(boundary.geometry_types))
    return f"{len(boundary.geometry_types)} edges: {', '.join(unique_types)}"
