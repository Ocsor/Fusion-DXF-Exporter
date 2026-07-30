"""Repeatable local coordinate-system selection for manufacturing faces."""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

import adsk.core

from ..utilities.geometry_utils import (
    canonical_direction,
    cross,
    dot,
    iter_fusion_collection,
    normalize,
    point_tuple,
    project_onto_plane,
    subtract,
)

Vector3 = Tuple[float, float, float]
GLOBAL_AXES = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


@dataclass
class LocalCoordinateSystem:
    """A right-handed frame attached to a planar manufacturing face."""

    origin: Vector3
    x_axis: Vector3
    y_axis: Vector3
    z_axis: Vector3
    source: str


def create_face_coordinate_system(face: Any, normal: Vector3) -> LocalCoordinateSystem:
    """Choose a stable X axis from the longest straight outer edge when possible."""

    z_axis = normalize(normal)
    edge_axis = _longest_outer_line_axis(face, z_axis)
    if edge_axis:
        x_axis = canonical_direction(edge_axis)
        source = "longest straight outer edge"
    else:
        least_aligned = min(GLOBAL_AXES, key=lambda axis: abs(dot(axis, z_axis)))
        x_axis = canonical_direction(project_onto_plane(least_aligned, z_axis))
        source = "projected global fallback axis"
    y_axis = normalize(cross(z_axis, x_axis))
    origin = point_tuple(face.centroid)
    return LocalCoordinateSystem(origin, x_axis, y_axis, z_axis, source)


def _longest_outer_line_axis(face: Any, normal: Vector3) -> Optional[Vector3]:
    outer_loop = None
    for loop in iter_fusion_collection(face.loops):
        if loop.isOuter:
            outer_loop = loop
            break
    if not outer_loop:
        return None

    longest_length = -1.0
    longest_axis: Optional[Vector3] = None
    for edge in iter_fusion_collection(outer_loop.edges):
        line = adsk.core.Line3D.cast(edge.geometry)
        if not line or not edge.startVertex or not edge.endVertex:
            continue
        start = point_tuple(edge.startVertex.geometry)
        end = point_tuple(edge.endVertex.geometry)
        projected = project_onto_plane(subtract(end, start), normal)
        try:
            axis = normalize(projected)
        except ValueError:
            continue
        if edge.length > longest_length:
            longest_length = edge.length
            longest_axis = axis
    return longest_axis
