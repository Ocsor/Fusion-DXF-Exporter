"""Planar face discovery and opposite-face selection."""

import math
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

import adsk.core

from .coordinate_system import create_face_coordinate_system
from ..models.analysis_models import ManufacturingFace
from ..utilities.fusion_utils import entity_token, temp_id
from ..utilities.geometry_utils import (
    are_opposed,
    are_parallel,
    dot,
    internal_area_to_mm2,
    iter_fusion_collection,
    point_tuple,
    normalize,
    signed_plane_offset,
)

Vector3 = Tuple[float, float, float]


@dataclass
class PlanarFaceRecord:
    """Internal record that may retain a Fusion face during analysis."""

    face: Any
    index: int
    area_internal: float
    origin: Vector3
    normal: Vector3


@dataclass
class MitreEdgeRecord:
    """A straight mitre edge and its acute angle to the support plane."""

    edge: Any
    angle_degrees: float


def collect_planar_faces(body: Any) -> List[PlanarFaceRecord]:
    """Return all planar body faces with outward normals."""

    records: List[PlanarFaceRecord] = []
    for index, face in enumerate(iter_fusion_collection(body.faces)):
        if not adsk.core.Plane.cast(face.geometry):
            continue
        success, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
        if not success or not normal:
            continue
        records.append(
            PlanarFaceRecord(
                face=face,
                index=index,
                area_internal=float(face.area),
                origin=point_tuple(face.pointOnFace),
                normal=point_tuple(normal),
            )
        )
    return records


def largest_planar_face(
    records: Iterable[PlanarFaceRecord],
) -> Optional[PlanarFaceRecord]:
    """Return the largest planar face by bounded B-Rep area."""

    return max(records, key=lambda record: record.area_internal, default=None)


def record_for_manual_face(
    records: Iterable[PlanarFaceRecord],
    selected_face: Any,
) -> Optional[PlanarFaceRecord]:
    """Match a manual face using temp ID, with token as a fallback."""

    selected_temp_id = temp_id(selected_face)
    selected_token = entity_token(selected_face)
    for record in records:
        if selected_temp_id is not None and temp_id(record.face) == selected_temp_id:
            return record
        if selected_token and entity_token(record.face) == selected_token:
            return record
    return None


def find_opposite_face(
    front: PlanarFaceRecord,
    records: Iterable[PlanarFaceRecord],
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
) -> Optional[PlanarFaceRecord]:
    """Find the furthest opposed support face behind the front face."""

    candidates = []
    for record in records:
        if record.index == front.index:
            continue
        if not are_opposed(front.normal, record.normal, angular_tolerance_radians):
            continue
        offset = signed_plane_offset(front.origin, record.origin, front.normal)
        if offset < -distance_tolerance_internal:
            candidates.append((abs(offset), record.area_internal, record))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def collect_rear_support_faces(
    front: PlanarFaceRecord,
    rear: PlanarFaceRecord,
    records: Iterable[PlanarFaceRecord],
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
) -> List[PlanarFaceRecord]:
    """Return every opposed planar face on the selected rear support plane."""

    rear_offset = signed_plane_offset(front.origin, rear.origin, front.normal)
    support_faces = []
    for record in records:
        if record.index == front.index:
            continue
        if not are_opposed(front.normal, record.normal, angular_tolerance_radians):
            continue
        offset = signed_plane_offset(front.origin, record.origin, front.normal)
        if abs(offset - rear_offset) <= distance_tolerance_internal:
            support_faces.append(record)
    return support_faces


def collect_coplanar_support_faces(
    reference: PlanarFaceRecord,
    records: Iterable[PlanarFaceRecord],
    distance_tolerance_internal: float,
    angular_tolerance_radians: float,
) -> List[PlanarFaceRecord]:
    """Return same-facing records on the reference support plane."""

    support_faces = []
    for record in records:
        if not are_parallel(
            reference.normal,
            record.normal,
            angular_tolerance_radians,
        ):
            continue
        if dot(reference.normal, record.normal) <= 0.0:
            continue
        offset = signed_plane_offset(
            reference.origin,
            record.origin,
            reference.normal,
        )
        if abs(offset) <= distance_tolerance_internal:
            support_faces.append(record)
    return support_faces


def collect_mitre_edges(
    records: Iterable[PlanarFaceRecord],
    front_support_faces: Iterable[PlanarFaceRecord],
    rear_support_faces: Iterable[PlanarFaceRecord],
    outside_profile: PlanarFaceRecord,
    angular_tolerance_radians: float,
) -> List[MitreEdgeRecord]:
    """Return straight outside edges belonging to full-thickness mitre faces."""

    front_faces = [record.face for record in front_support_faces]
    rear_faces = [record.face for record in rear_support_faces]
    support_keys = {
        _entity_key(face)
        for face in front_faces + rear_faces
    }
    mitre_edges = {}
    cosine_limit = math.cos(angular_tolerance_radians)
    sine_limit = math.sin(angular_tolerance_radians)
    outside_key = _entity_key(outside_profile.face)
    for record in records:
        if _entity_key(record.face) in support_keys:
            continue
        alignment = abs(
            dot(
                normalize(record.normal),
                normalize(outside_profile.normal),
            )
        )
        if alignment <= sine_limit or alignment >= cosine_limit:
            continue
        if not _face_touches_any(record.face, front_faces):
            continue
        if not _face_touches_any(record.face, rear_faces):
            continue
        angle_degrees = _mitre_angle_degrees(
            record.normal,
            outside_profile.normal,
        )
        for edge in iter_fusion_collection(record.face.edges):
            if not adsk.core.Line3D.cast(edge.geometry):
                continue
            adjacent_keys = {
                _entity_key(face)
                for face in iter_fusion_collection(edge.faces)
            }
            if outside_key in adjacent_keys:
                mitre_edges[_entity_key(edge)] = MitreEdgeRecord(
                    edge=edge,
                    angle_degrees=angle_degrees,
                )
    return list(mitre_edges.values())


def _mitre_angle_degrees(
    face_normal: Vector3,
    support_normal: Vector3,
) -> float:
    alignment = abs(dot(normalize(face_normal), normalize(support_normal)))
    limited_alignment = max(0.0, min(1.0, alignment))
    return math.degrees(math.acos(limited_alignment))


def to_manufacturing_face(
    record: PlanarFaceRecord,
    role: str,
) -> ManufacturingFace:
    """Convert an internal face record to a serializable model."""

    frame = create_face_coordinate_system(record.face, record.normal)
    return ManufacturingFace(
        role=role,
        face_index=record.index,
        temp_id=temp_id(record.face),
        entity_token=entity_token(record.face),
        area_internal=record.area_internal,
        area_mm2=internal_area_to_mm2(record.area_internal),
        origin=frame.origin,
        normal=frame.z_axis,
        x_axis=frame.x_axis,
        y_axis=frame.y_axis,
    )


def _face_touches_any(face: Any, target_faces: Iterable[Any]) -> bool:
    target_keys = {_entity_key(target) for target in target_faces}
    for edge in iter_fusion_collection(face.edges):
        for adjacent_face in iter_fusion_collection(edge.faces):
            if _entity_key(adjacent_face) in target_keys:
                return True
    return False


def _entity_key(entity: Any) -> str:
    entity_temp_id = temp_id(entity)
    if entity_temp_id is not None:
        return f"temp:{entity_temp_id}"
    token = entity_token(entity)
    return f"token:{token}" if token else f"object:{id(entity)}"
