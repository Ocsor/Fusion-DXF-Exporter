"""Temporary sketch creation for operation-category DXF exports."""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import adsk.core
import adsk.fusion

from ..models.analysis_models import BodyAnalysis, DetectedOperation, OperationType
from ..utilities.layer_utils import layer_name_for_operation

PHASE_THREE_OPERATION_TYPES = (
    OperationType.CUT_OUTSIDE,
    OperationType.CUT_INSIDE,
    OperationType.FRONT_POCKET,
    OperationType.FRONT_REBATE,
    OperationType.BACK_POCKET,
    OperationType.BACK_REBATE,
    OperationType.MITRE,
)
DEFAULT_MITRE_OFFSET_INTERNAL = 0.05
DEFAULT_REBATE_OFFSET_INTERNAL = 0.03
REBATE_EDGE_EXTENSION_INTERNAL = 0.5
REBATE_CONTACT_TOLERANCE_INTERNAL = 0.001
MITRE_EXTENSION_INTERNAL = 0.2
MITRE_ENDPOINT_TOLERANCE_INTERNAL = 0.001


@dataclass
class TemporarySketchSet:
    """Fusion sketches and entities created for one body export."""

    sketches: Dict[str, Any] = field(default_factory=dict)
    entities: Dict[str, List[Any]] = field(default_factory=dict)
    rotation_radians: float = 0.0
    translation: Tuple[float, float] = (0.0, 0.0)

    def all_sketches(self) -> Iterable[Any]:
        """Iterate sketches without exposing dictionary assumptions."""

        return self.sketches.values()


def temporary_sketch_name(layer_name: str) -> str:
    """Return the controlled sketch name for an export layer."""

    return f"DXF_TEMP_{layer_name}"


def build_phase_three_sketches(
    design: Any,
    body: Any,
    analysis: BodyAnalysis,
    include_front_machining: bool,
    include_rear_machining: bool,
    include_depth_in_layer_names: bool,
    mitre_offset_internal: float = DEFAULT_MITRE_OFFSET_INTERNAL,
    rebate_offset_internal: float = DEFAULT_REBATE_OFFSET_INTERNAL,
    sketch_set: Optional[TemporarySketchSet] = None,
) -> TemporarySketchSet:
    """Create aligned, origin-shifted sketches for supported operations."""

    if not analysis.front_face or analysis.front_face.temp_id is None:
        raise ValueError("The analysis does not contain a resolvable front face.")
    front_face = _resolve_face(body, analysis.front_face.temp_id)
    if not front_face:
        raise RuntimeError("The analysed front face could not be resolved.")

    operations_by_layer = _supported_operations(
        analysis.operations,
        include_front_machining,
        include_rear_machining,
        include_depth_in_layer_names,
    )
    if not operations_by_layer.get(OperationType.CUT_OUTSIDE.value):
        raise RuntimeError("No outside profile is available for export.")

    sketch_set = sketch_set or TemporarySketchSet()
    root_component = design.rootComponent
    for layer_name, operations in operations_by_layer.items():
        sketch = root_component.sketches.add(front_face)
        if not sketch:
            raise RuntimeError(
                f"Fusion did not create {temporary_sketch_name(layer_name)}."
            )
        sketch.name = temporary_sketch_name(layer_name)
        sketch_set.sketches[layer_name] = sketch
        _remove_automatic_face_curves(sketch)
        edges = _resolve_operation_edges(body, operations)
        entities = _project_unlinked(sketch, edges)
        if not entities:
            raise RuntimeError(
                f"No entities were projected for {layer_name}."
            )
        sketch_set.entities[layer_name] = entities

    outside_layer = OperationType.CUT_OUTSIDE.value
    outside_sketch = sketch_set.sketches[outside_layer]
    _prepare_rebate_geometry(sketch_set, rebate_offset_internal)
    _prepare_mitre_guides(
        sketch_set,
        outside_layer,
        mitre_offset_internal,
    )
    rotation = _stable_axis_rotation(outside_sketch, analysis)
    sketch_set.rotation_radians = rotation
    for layer_name, sketch in sketch_set.sketches.items():
        _rotate_entities(sketch, sketch_set.entities[layer_name], rotation)

    outside_entities = sketch_set.entities[outside_layer]
    minimum_x, minimum_y = _entity_minimum(outside_entities)
    translation = (-minimum_x, -minimum_y)
    sketch_set.translation = translation
    for layer_name, sketch in sketch_set.sketches.items():
        _translate_entities(
            sketch,
            sketch_set.entities[layer_name],
            translation[0],
            translation[1],
        )
    return sketch_set


def cleanup_temporary_sketches(sketch_set: TemporarySketchSet) -> List[str]:
    """Delete every valid temporary sketch and return cleanup errors."""

    errors: List[str] = []
    for sketch in reversed(list(sketch_set.all_sketches())):
        try:
            if getattr(sketch, "isValid", False) and not sketch.deleteMe():
                errors.append(f"Fusion did not delete temporary sketch {sketch.name}.")
        except Exception as error:
            errors.append(f"Could not delete temporary sketch: {error}")
    return errors


def _supported_operations(
    operations: Iterable[DetectedOperation],
    include_front_machining: bool,
    include_rear_machining: bool,
    include_depth_in_layer_names: bool,
) -> Dict[str, List[DetectedOperation]]:
    grouped: Dict[str, List[DetectedOperation]] = {}
    for operation in operations:
        if operation.operation_type not in PHASE_THREE_OPERATION_TYPES:
            continue
        if (
            operation.operation_type
            in {OperationType.FRONT_POCKET, OperationType.FRONT_REBATE}
            and not include_front_machining
        ):
            continue
        if (
            operation.operation_type
            in {OperationType.BACK_POCKET, OperationType.BACK_REBATE}
            and not include_rear_machining
        ):
            continue
        layer_name = layer_name_for_operation(
            operation.operation_type,
            operation.depth_mm,
            include_depth_in_layer_names,
            angle_degrees=operation.angle_degrees,
        )
        grouped.setdefault(layer_name, []).append(operation)
    return grouped


def _prepare_rebate_geometry(
    sketch_set: TemporarySketchSet,
    rebate_offset_internal: float,
) -> None:
    if rebate_offset_internal < 0.0:
        raise ValueError("The rebate offset cannot be negative.")
    if rebate_offset_internal <= 1e-12:
        return
    rebate_layers = [
        layer_name
        for layer_name in sketch_set.entities
        if (
            layer_name
            in {
                OperationType.FRONT_REBATE.value,
                OperationType.BACK_REBATE.value,
            }
            or layer_name.startswith(f"{OperationType.FRONT_REBATE.value}_")
            or layer_name.startswith(f"{OperationType.BACK_REBATE.value}_")
        )
    ]
    outside_lines = [
        line
        for entity in sketch_set.entities[OperationType.CUT_OUTSIDE.value]
        if (line := _sketch_line_points(entity)) is not None
    ]
    for layer_name in rebate_layers:
        sketch = sketch_set.sketches[layer_name]
        source_entities = sketch_set.entities[layer_name]
        expanded_entities = []
        connected_groups = _connected_curve_groups(sketch, source_entities)
        line_loops = [
            _ordered_line_loop(connected_entities)
            for connected_entities in connected_groups
        ]
        group_bounds = [
            _entity_bounds(connected_entities)
            for connected_entities in connected_groups
        ]
        for group_index, connected_entities in enumerate(connected_groups):
            offset_inward = _loop_is_nested(
                line_loops[group_index],
                line_loops,
                group_bounds,
                group_index,
            )
            offset_segments = _directional_rebate_segments(
                connected_entities,
                outside_lines,
                rebate_offset_internal,
                offset_inward,
            )
            if offset_segments is None:
                offset_curves = _uniform_rebate_offset(
                    sketch,
                    connected_entities,
                    rebate_offset_internal,
                    layer_name,
                    offset_inward,
                )
                if not offset_inward:
                    offset_curves = _extend_curved_rebate_contact_lines(
                        sketch,
                        connected_entities,
                        offset_curves,
                        outside_lines,
                        rebate_offset_internal,
                        layer_name,
                    )
            else:
                offset_curves = []
                for start, end in offset_segments:
                    offset_curve = sketch.sketchCurves.sketchLines.addByTwoPoints(
                        adsk.core.Point3D.create(start[0], start[1], 0.0),
                        adsk.core.Point3D.create(end[0], end[1], 0.0),
                    )
                    if not offset_curve:
                        raise RuntimeError(
                            f"Fusion did not extend rebate geometry on {layer_name}."
                        )
                    offset_curves.append(offset_curve)
            for entity in connected_entities:
                if not entity.deleteMe():
                    raise RuntimeError(
                        f"Could not replace rebate geometry on {layer_name}."
                    )
            expanded_entities.extend(offset_curves)
        sketch_set.entities[layer_name] = expanded_entities


def _directional_rebate_segments(
    entities: Iterable[Any],
    outside_lines: Iterable[
        Tuple[Tuple[float, float], Tuple[float, float]]
    ],
    rebate_offset_internal: float,
    offset_inward: bool = False,
) -> Optional[List[Tuple[Tuple[float, float], Tuple[float, float]]]]:
    vertices = _ordered_line_loop(entities)
    if vertices is None:
        return None
    signed_area = sum(
        start[0] * end[1] - end[0] * start[1]
        for start, end in zip(vertices, vertices[1:] + vertices[:1])
    )
    if abs(signed_area) <= 1e-12:
        return None
    outside_lines = list(outside_lines)
    offset_lines = []
    for start, end in zip(vertices, vertices[1:] + vertices[:1]):
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length = math.hypot(delta_x, delta_y)
        if length <= 1e-12:
            return None
        if signed_area > 0.0:
            normal_x = delta_y / length
            normal_y = -delta_x / length
        else:
            normal_x = -delta_y / length
            normal_y = delta_x / length
        offset_distance = (
            REBATE_EDGE_EXTENSION_INTERNAL
            if _line_touches_outside((start, end), outside_lines)
            else (
                -rebate_offset_internal
                if offset_inward
                else rebate_offset_internal
            )
        )
        offset_lines.append(
            (
                (
                    start[0] + normal_x * offset_distance,
                    start[1] + normal_y * offset_distance,
                ),
                (
                    end[0] + normal_x * offset_distance,
                    end[1] + normal_y * offset_distance,
                ),
            )
        )
    offset_vertices = []
    for index, line in enumerate(offset_lines):
        previous_line = offset_lines[index - 1]
        intersection = _line_intersection(previous_line, line)
        if intersection is None:
            return None
        offset_vertices.append(intersection)
    return list(
        zip(offset_vertices, offset_vertices[1:] + offset_vertices[:1])
    )


def _loop_is_nested(
    vertices: Optional[List[Tuple[float, float]]],
    all_loops: List[Optional[List[Tuple[float, float]]]],
    all_bounds: List[Tuple[float, float, float, float]],
    loop_index: int,
) -> bool:
    current_bounds = all_bounds[loop_index]
    test_point = vertices[0] if vertices else (
        (current_bounds[0] + current_bounds[2]) / 2.0,
        (current_bounds[1] + current_bounds[3]) / 2.0,
    )
    containing_loop_count = sum(
        1
        for candidate_index, candidate_vertices in enumerate(all_loops)
        if (
            candidate_index != loop_index
            and (
                _point_in_polygon(test_point, candidate_vertices)
                if candidate_vertices
                else _bounds_contain(all_bounds[candidate_index], current_bounds)
            )
        )
    )
    return containing_loop_count % 2 == 1


def _bounds_contain(
    outer: Tuple[float, float, float, float],
    inner: Tuple[float, float, float, float],
) -> bool:
    tolerance = 1e-9
    contains = (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )
    strictly_larger = (
        outer[0] < inner[0] - tolerance
        or outer[1] < inner[1] - tolerance
        or outer[2] > inner[2] + tolerance
        or outer[3] > inner[3] + tolerance
    )
    return contains and strictly_larger


def _point_in_polygon(
    point: Tuple[float, float],
    vertices: List[Tuple[float, float]],
) -> bool:
    point_x, point_y = point
    inside = False
    for start, end in zip(vertices, vertices[1:] + vertices[:1]):
        if (start[1] > point_y) == (end[1] > point_y):
            continue
        intersection_x = (
            (end[0] - start[0])
            * (point_y - start[1])
            / (end[1] - start[1])
            + start[0]
        )
        if point_x < intersection_x:
            inside = not inside
    return inside


def _ordered_line_loop(
    entities: Iterable[Any],
) -> Optional[List[Tuple[float, float]]]:
    records = []
    for entity in entities:
        points = _sketch_line_points(entity)
        if points is None:
            return None
        records.append(points)
    if len(records) < 3:
        return None
    first_start, first_end = records.pop(0)
    vertices = [first_start, first_end]
    while records:
        current = vertices[-1]
        matching_index = None
        next_point = None
        for index, (start, end) in enumerate(records):
            if _points_are_close(current, start):
                matching_index = index
                next_point = end
                break
            if _points_are_close(current, end):
                matching_index = index
                next_point = start
                break
        if matching_index is None or next_point is None:
            return None
        records.pop(matching_index)
        vertices.append(next_point)
    if not _points_are_close(vertices[-1], vertices[0]):
        return None
    return vertices[:-1]


def _sketch_line_points(
    entity: Any,
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    sketch_line = adsk.fusion.SketchLine.cast(entity)
    if not sketch_line:
        return None
    start = sketch_line.startSketchPoint.geometry
    end = sketch_line.endSketchPoint.geometry
    return (
        (float(start.x), float(start.y)),
        (float(end.x), float(end.y)),
    )


def _line_touches_outside(
    line: Tuple[Tuple[float, float], Tuple[float, float]],
    outside_lines: Iterable[
        Tuple[Tuple[float, float], Tuple[float, float]]
    ],
) -> bool:
    start, end = line
    midpoint = (
        (start[0] + end[0]) / 2.0,
        (start[1] + end[1]) / 2.0,
    )
    return all(
        any(
            _point_on_segment(
                point,
                outside_line,
                REBATE_CONTACT_TOLERANCE_INTERNAL,
            )
            for outside_line in outside_lines
        )
        for point in (start, midpoint, end)
    )


def _point_on_segment(
    point: Tuple[float, float],
    segment: Tuple[Tuple[float, float], Tuple[float, float]],
    tolerance: float,
) -> bool:
    start, end = segment
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length = math.hypot(delta_x, delta_y)
    if length <= 1e-12:
        return _points_are_close(point, start, tolerance)
    relative_x = point[0] - start[0]
    relative_y = point[1] - start[1]
    perpendicular_distance = abs(
        relative_x * delta_y - relative_y * delta_x
    ) / length
    if perpendicular_distance > tolerance:
        return False
    projection = (relative_x * delta_x + relative_y * delta_y) / length
    return -tolerance <= projection <= length + tolerance


def _points_are_close(
    first: Tuple[float, float],
    second: Tuple[float, float],
    tolerance: float = REBATE_CONTACT_TOLERANCE_INTERNAL,
) -> bool:
    return math.hypot(
        first[0] - second[0],
        first[1] - second[1],
    ) <= tolerance


def _uniform_rebate_offset(
    sketch: Any,
    connected_entities: List[Any],
    rebate_offset_internal: float,
    layer_name: str,
    offset_inward: bool = False,
) -> List[Any]:
    minimum_x, minimum_y, maximum_x, maximum_y = _entity_bounds(
        connected_entities
    )
    if offset_inward:
        direction_point = adsk.core.Point3D.create(
            (minimum_x + maximum_x) / 2.0,
            (minimum_y + maximum_y) / 2.0,
            0.0,
        )
    else:
        direction_margin = max(rebate_offset_internal * 2.0, 0.1)
        direction_point = adsk.core.Point3D.create(
            minimum_x - direction_margin,
            (minimum_y + maximum_y) / 2.0,
            0.0,
        )
    constraints = sketch.geometricConstraints
    existing_constraint_count = constraints.count
    offset_curves = list(
        sketch.offset(
            _object_collection(connected_entities),
            direction_point,
            rebate_offset_internal,
        )
    )
    if not offset_curves:
        raise RuntimeError(
            f"Fusion did not offset rebate geometry on {layer_name}."
        )
    added_constraints = [
        constraints.item(index)
        for index in range(existing_constraint_count, constraints.count)
    ]
    for constraint in reversed(added_constraints):
        if getattr(constraint, "isDeletable", False):
            if not constraint.deleteMe():
                raise RuntimeError(
                    f"Could not detach rebate offset on {layer_name}."
                )
    if not all(getattr(curve, "isValid", True) for curve in offset_curves):
        raise RuntimeError(
            f"Fusion invalidated rebate offset geometry on {layer_name}."
        )
    offset_bounds = _entity_bounds(offset_curves)
    bounds_tolerance = 1e-9
    offset_moved_wrong_way = (
        offset_bounds[0] < minimum_x - bounds_tolerance
        or offset_bounds[1] < minimum_y - bounds_tolerance
        or offset_bounds[2] > maximum_x + bounds_tolerance
        or offset_bounds[3] > maximum_y + bounds_tolerance
        if offset_inward
        else (
            offset_bounds[0] > minimum_x + bounds_tolerance
            or offset_bounds[1] > minimum_y + bounds_tolerance
            or offset_bounds[2] < maximum_x - bounds_tolerance
            or offset_bounds[3] < maximum_y - bounds_tolerance
        )
    )
    if offset_moved_wrong_way:
        raise RuntimeError(
            f"Fusion offset rebate geometry the wrong way on {layer_name}."
        )
    return offset_curves


def _extend_curved_rebate_contact_lines(
    sketch: Any,
    source_entities: Iterable[Any],
    offset_curves: List[Any],
    outside_lines: Iterable[
        Tuple[Tuple[float, float], Tuple[float, float]]
    ],
    rebate_offset_internal: float,
    layer_name: str,
) -> List[Any]:
    outside_lines = list(outside_lines)
    contact_lines = [
        line
        for entity in source_entities
        if (line := _sketch_line_points(entity)) is not None
        and _line_touches_outside(line, outside_lines)
    ]
    for source_line in contact_lines:
        offset_line = _matching_offset_line(
            source_line,
            offset_curves,
            rebate_offset_internal,
        )
        if offset_line is None:
            raise RuntimeError(
                f"Could not identify an offset rebate edge on {layer_name}."
            )
        offset_line_points = _sketch_line_points(offset_line)
        if offset_line_points is None:
            raise RuntimeError(
                f"A rebate contact edge was not straight on {layer_name}."
            )
        adjacent_lines = []
        for endpoint in offset_line_points:
            matches = [
                entity
                for entity in offset_curves
                if _entity_key(entity) != _entity_key(offset_line)
                and (points := _sketch_line_points(entity)) is not None
                and any(_points_are_close(endpoint, point) for point in points)
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    "A curved rebate edge touching the outside profile must "
                    f"have one straight adjoining edge at each end on {layer_name}."
                )
            adjacent_lines.append(matches[0])

        desired_line = _extended_contact_line(source_line, offset_line_points)
        replacement_adjacent_lines = []
        contact_endpoints = []
        for endpoint, adjacent_line in zip(offset_line_points, adjacent_lines):
            adjacent_points = _sketch_line_points(adjacent_line)
            if adjacent_points is None:
                raise RuntimeError(
                    f"A rebate adjoining edge was not straight on {layer_name}."
                )
            far_endpoint = (
                adjacent_points[1]
                if _points_are_close(endpoint, adjacent_points[0])
                else adjacent_points[0]
            )
            intersection = _line_intersection(adjacent_points, desired_line)
            if intersection is None:
                raise RuntimeError(
                    f"A rebate adjoining edge is parallel on {layer_name}."
                )
            replacement = sketch.sketchCurves.sketchLines.addByTwoPoints(
                adsk.core.Point3D.create(far_endpoint[0], far_endpoint[1], 0.0),
                adsk.core.Point3D.create(intersection[0], intersection[1], 0.0),
            )
            if not replacement:
                raise RuntimeError(
                    f"Fusion did not extend a rebate side edge on {layer_name}."
                )
            replacement_adjacent_lines.append(replacement)
            contact_endpoints.append(intersection)

        replacement_contact_line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            adsk.core.Point3D.create(
                contact_endpoints[0][0],
                contact_endpoints[0][1],
                0.0,
            ),
            adsk.core.Point3D.create(
                contact_endpoints[1][0],
                contact_endpoints[1][1],
                0.0,
            ),
        )
        if not replacement_contact_line:
            raise RuntimeError(
                f"Fusion did not extend a rebate contact edge on {layer_name}."
            )

        replaced_keys = {
            _entity_key(offset_line),
            *(_entity_key(entity) for entity in adjacent_lines),
        }
        retained_curves = [
            entity
            for entity in offset_curves
            if _entity_key(entity) not in replaced_keys
        ]
        for entity in [offset_line, *adjacent_lines]:
            if not entity.deleteMe():
                raise RuntimeError(
                    f"Could not replace curved rebate geometry on {layer_name}."
                )
        offset_curves = retained_curves
        offset_curves.extend(
            [replacement_contact_line, *replacement_adjacent_lines]
        )
    return offset_curves


def _matching_offset_line(
    source_line: Tuple[Tuple[float, float], Tuple[float, float]],
    offset_curves: Iterable[Any],
    rebate_offset_internal: float,
) -> Optional[Any]:
    source_start, source_end = source_line
    delta_x = source_end[0] - source_start[0]
    delta_y = source_end[1] - source_start[1]
    source_length = math.hypot(delta_x, delta_y)
    if source_length <= 1e-12:
        return None
    unit_x = delta_x / source_length
    unit_y = delta_y / source_length
    candidates = []
    for entity in offset_curves:
        points = _sketch_line_points(entity)
        if points is None:
            continue
        candidate_start, candidate_end = points
        candidate_delta_x = candidate_end[0] - candidate_start[0]
        candidate_delta_y = candidate_end[1] - candidate_start[1]
        candidate_length = math.hypot(candidate_delta_x, candidate_delta_y)
        if candidate_length <= 1e-12:
            continue
        parallel_error = abs(
            unit_x * candidate_delta_y / candidate_length
            - unit_y * candidate_delta_x / candidate_length
        )
        if parallel_error > 1e-6:
            continue
        tangent_positions = [
            (point[0] - source_start[0]) * unit_x
            + (point[1] - source_start[1]) * unit_y
            for point in points
        ]
        if (
            max(tangent_positions) < -REBATE_CONTACT_TOLERANCE_INTERNAL
            or min(tangent_positions)
            > source_length + REBATE_CONTACT_TOLERANCE_INTERNAL
        ):
            continue
        candidate_midpoint = (
            (candidate_start[0] + candidate_end[0]) / 2.0,
            (candidate_start[1] + candidate_end[1]) / 2.0,
        )
        perpendicular_distance = abs(
            (candidate_midpoint[0] - source_start[0]) * -unit_y
            + (candidate_midpoint[1] - source_start[1]) * unit_x
        )
        candidates.append(
            (
                abs(perpendicular_distance - rebate_offset_internal),
                entity,
            )
        )
    return (
        min(candidates, key=lambda candidate: candidate[0])[1]
        if candidates
        else None
    )


def _extended_contact_line(
    source_line: Tuple[Tuple[float, float], Tuple[float, float]],
    offset_line: Tuple[Tuple[float, float], Tuple[float, float]],
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    source_start, source_end = source_line
    delta_x = source_end[0] - source_start[0]
    delta_y = source_end[1] - source_start[1]
    length = math.hypot(delta_x, delta_y)
    normal_x = -delta_y / length
    normal_y = delta_x / length
    source_midpoint = (
        (source_start[0] + source_end[0]) / 2.0,
        (source_start[1] + source_end[1]) / 2.0,
    )
    offset_midpoint = (
        (offset_line[0][0] + offset_line[1][0]) / 2.0,
        (offset_line[0][1] + offset_line[1][1]) / 2.0,
    )
    if (
        (offset_midpoint[0] - source_midpoint[0]) * normal_x
        + (offset_midpoint[1] - source_midpoint[1]) * normal_y
    ) < 0.0:
        normal_x = -normal_x
        normal_y = -normal_y
    return (
        (
            source_start[0] + normal_x * REBATE_EDGE_EXTENSION_INTERNAL,
            source_start[1] + normal_y * REBATE_EDGE_EXTENSION_INTERNAL,
        ),
        (
            source_end[0] + normal_x * REBATE_EDGE_EXTENSION_INTERNAL,
            source_end[1] + normal_y * REBATE_EDGE_EXTENSION_INTERNAL,
        ),
    )


def _connected_curve_groups(
    sketch: Any,
    entities: Iterable[Any],
) -> List[List[Any]]:
    entities_by_key = {_entity_key(entity): entity for entity in entities}
    remaining_keys = set(entities_by_key)
    groups = []
    while remaining_keys:
        seed_key = next(iter(remaining_keys))
        seed = entities_by_key[seed_key]
        connected = list(sketch.findConnectedCurves(seed))
        group = [
            entity
            for entity in connected
            if _entity_key(entity) in entities_by_key
        ]
        if not group:
            group = [seed]
        group_keys = {_entity_key(entity) for entity in group}
        remaining_keys.difference_update(group_keys)
        groups.append(group)
    return groups


def _entity_key(entity: Any) -> str:
    return getattr(entity, "entityToken", "") or str(id(entity))


def _prepare_mitre_guides(
    sketch_set: TemporarySketchSet,
    outside_layer: str,
    mitre_offset_internal: float,
) -> None:
    mitre_layers = [
        layer_name
        for layer_name in sketch_set.entities
        if (
            layer_name == OperationType.MITRE.value
            or layer_name.startswith(f"{OperationType.MITRE.value}_")
        )
    ]
    if not mitre_layers:
        return
    outside_entities = sketch_set.entities[outside_layer]
    minimum_x, minimum_y, maximum_x, maximum_y = _entity_bounds(
        outside_entities
    )
    centre_x = (minimum_x + maximum_x) / 2.0
    centre_y = (minimum_y + maximum_y) / 2.0
    source_records = [
        (layer_name, entity)
        for layer_name in mitre_layers
        for entity in sketch_set.entities[layer_name]
    ]
    source_lines = []
    for _, entity in source_records:
        sketch_line = adsk.fusion.SketchLine.cast(entity)
        if not sketch_line:
            raise RuntimeError("A detected mitre edge did not project as a line.")
        start = sketch_line.startSketchPoint.geometry
        end = sketch_line.endSketchPoint.geometry
        source_lines.append(
            (
                (float(start.x), float(start.y)),
                (float(end.x), float(end.y)),
            )
        )

    guide_segments = _mitre_guide_segments(
        source_lines,
        (centre_x, centre_y),
        mitre_offset_internal,
        mitre_angle_keys=[layer_name for layer_name, _ in source_records],
    )
    guide_entities = {layer_name: [] for layer_name in mitre_layers}
    for source_record, guide_segment in zip(source_records, guide_segments):
        layer_name, entity = source_record
        sketch = sketch_set.sketches[layer_name]
        sketch_line = adsk.fusion.SketchLine.cast(entity)
        guide_start_xy, guide_end_xy = guide_segment
        guide_start = adsk.core.Point3D.create(
            guide_start_xy[0],
            guide_start_xy[1],
            0.0,
        )
        guide_end = adsk.core.Point3D.create(
            guide_end_xy[0],
            guide_end_xy[1],
            0.0,
        )
        guide = sketch.sketchCurves.sketchLines.addByTwoPoints(
            guide_start,
            guide_end,
        )
        if not guide:
            raise RuntimeError("Fusion did not create a MITRE guide line.")
        if not sketch_line.deleteMe():
            raise RuntimeError("Fusion did not replace a projected mitre edge.")
        guide_entities[layer_name].append(guide)
    for layer_name, entities in guide_entities.items():
        sketch_set.entities[layer_name] = entities


def _mitre_guide_segments(
    source_lines: Iterable[Tuple[Tuple[float, float], Tuple[float, float]]],
    outside_centre: Tuple[float, float],
    mitre_offset_internal: float = DEFAULT_MITRE_OFFSET_INTERNAL,
    mitre_angle_keys: Optional[Iterable[str]] = None,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    if mitre_offset_internal < 0.0:
        raise ValueError("The mitre guide offset cannot be negative.")
    source_lines = list(source_lines)
    angle_keys = (
        list(mitre_angle_keys)
        if mitre_angle_keys is not None
        else [None] * len(source_lines)
    )
    if len(angle_keys) != len(source_lines):
        raise ValueError("Each mitre guide must have one angle key.")
    records = []
    centre_x, centre_y = outside_centre
    for line_index, (start, end) in enumerate(source_lines):
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length = math.hypot(delta_x, delta_y)
        if length <= 1e-12:
            raise RuntimeError("A detected mitre guide has zero length.")
        unit_x = delta_x / length
        unit_y = delta_y / length
        normal_x = -unit_y
        normal_y = unit_x
        midpoint_x = (start[0] + end[0]) / 2.0
        midpoint_y = (start[1] + end[1]) / 2.0
        if (
            (midpoint_x - centre_x) * normal_x
            + (midpoint_y - centre_y) * normal_y
        ) < 0.0:
            normal_x = -normal_x
            normal_y = -normal_y
        offset_start = (
            start[0] + normal_x * mitre_offset_internal,
            start[1] + normal_y * mitre_offset_internal,
        )
        offset_end = (
            end[0] + normal_x * mitre_offset_internal,
            end[1] + normal_y * mitre_offset_internal,
        )
        records.append(
            {
                "source": (start, end),
                "offset": (offset_start, offset_end),
                "angle_key": angle_keys[line_index],
                "guide": [
                    (
                        offset_start[0] - unit_x * MITRE_EXTENSION_INTERNAL,
                        offset_start[1] - unit_y * MITRE_EXTENSION_INTERNAL,
                    ),
                    (
                        offset_end[0] + unit_x * MITRE_EXTENSION_INTERNAL,
                        offset_end[1] + unit_y * MITRE_EXTENSION_INTERNAL,
                    ),
                ],
            }
        )

    endpoint_groups: List[List[Tuple[int, int]]] = []
    for record_index, record in enumerate(records):
        for endpoint_index, source_point in enumerate(record["source"]):
            matching_group = None
            for group in endpoint_groups:
                other_record, other_endpoint = group[0]
                other_point = records[other_record]["source"][other_endpoint]
                if math.hypot(
                    source_point[0] - other_point[0],
                    source_point[1] - other_point[1],
                ) <= MITRE_ENDPOINT_TOLERANCE_INTERNAL:
                    matching_group = group
                    break
            if matching_group is None:
                matching_group = []
                endpoint_groups.append(matching_group)
            matching_group.append((record_index, endpoint_index))

    for group in endpoint_groups:
        if len(group) != 2 or group[0][0] == group[1][0]:
            continue
        first_record_index, first_endpoint_index = group[0]
        second_record_index, second_endpoint_index = group[1]
        if (
            records[first_record_index]["angle_key"]
            != records[second_record_index]["angle_key"]
        ):
            continue
        intersection = _line_intersection(
            records[first_record_index]["offset"],
            records[second_record_index]["offset"],
        )
        if intersection is None:
            continue
        records[first_record_index]["guide"][
            first_endpoint_index
        ] = intersection
        records[second_record_index]["guide"][
            second_endpoint_index
        ] = intersection

    return [
        (tuple(record["guide"][0]), tuple(record["guide"][1]))
        for record in records
    ]


def _line_intersection(
    first_line: Tuple[Tuple[float, float], Tuple[float, float]],
    second_line: Tuple[Tuple[float, float], Tuple[float, float]],
) -> Optional[Tuple[float, float]]:
    first_start, first_end = first_line
    second_start, second_end = second_line
    first_delta = (
        first_end[0] - first_start[0],
        first_end[1] - first_start[1],
    )
    second_delta = (
        second_end[0] - second_start[0],
        second_end[1] - second_start[1],
    )
    denominator = (
        first_delta[0] * second_delta[1]
        - first_delta[1] * second_delta[0]
    )
    if abs(denominator) <= 1e-12:
        return None
    between_starts = (
        second_start[0] - first_start[0],
        second_start[1] - first_start[1],
    )
    first_parameter = (
        between_starts[0] * second_delta[1]
        - between_starts[1] * second_delta[0]
    ) / denominator
    return (
        first_start[0] + first_parameter * first_delta[0],
        first_start[1] + first_parameter * first_delta[1],
    )


def _resolve_face(body: Any, face_temp_id: int) -> Optional[Any]:
    for entity in body.findByTempId(face_temp_id):
        face = adsk.fusion.BRepFace.cast(entity)
        if face:
            return face
    return None


def _resolve_operation_edges(
    body: Any,
    operations: Iterable[DetectedOperation],
) -> List[Any]:
    edges: List[Any] = []
    seen_temp_ids = set()
    for operation in operations:
        for edge_temp_id in operation.source_edge_temp_ids:
            if edge_temp_id in seen_temp_ids:
                continue
            resolved_edge = None
            for entity in body.findByTempId(edge_temp_id):
                edge = adsk.fusion.BRepEdge.cast(entity)
                if edge:
                    resolved_edge = edge
                    break
            if not resolved_edge:
                raise RuntimeError(
                    f"Source edge temp ID {edge_temp_id} could not be resolved."
                )
            seen_temp_ids.add(edge_temp_id)
            edges.append(resolved_edge)
    return edges


def _remove_automatic_face_curves(sketch: Any) -> None:
    curves = [
        sketch.sketchCurves.item(index)
        for index in range(sketch.sketchCurves.count)
    ]
    for curve in curves:
        if not curve.isVisible and curve.isDeletable:
            curve.deleteMe()


def _project_unlinked(sketch: Any, edges: List[Any]) -> List[Any]:
    project2 = getattr(sketch, "project2", None)
    if callable(project2):
        return list(project2(edges, False))

    source_collection = adsk.core.ObjectCollection.create()
    for edge in edges:
        source_collection.add(edge)
    projected = sketch.project(source_collection)
    entities = [projected.item(index) for index in range(projected.count)]
    for entity in entities:
        if getattr(entity, "isReference", False):
            entity.isReference = False
    return entities


def _stable_axis_rotation(sketch: Any, analysis: BodyAnalysis) -> float:
    face = analysis.front_face
    if not face:
        return 0.0
    origin = adsk.core.Point3D.create(*face.origin)
    x_axis_point = adsk.core.Point3D.create(
        face.origin[0] + face.x_axis[0],
        face.origin[1] + face.x_axis[1],
        face.origin[2] + face.x_axis[2],
    )
    sketch_origin = sketch.modelToSketchSpace(origin)
    sketch_axis_point = sketch.modelToSketchSpace(x_axis_point)
    axis_x = sketch_axis_point.x - sketch_origin.x
    axis_y = sketch_axis_point.y - sketch_origin.y
    if math.hypot(axis_x, axis_y) <= 1e-12:
        raise RuntimeError("Could not map the stable X axis into sketch space.")
    return -math.atan2(axis_y, axis_x)


def _rotate_entities(sketch: Any, entities: List[Any], angle: float) -> None:
    if abs(angle) <= 1e-12:
        return
    transform = adsk.core.Matrix3D.create()
    transform.setToRotation(
        angle,
        adsk.core.Vector3D.create(0.0, 0.0, 1.0),
        adsk.core.Point3D.create(0.0, 0.0, 0.0),
    )
    if not sketch.move(_object_collection(entities), transform):
        raise RuntimeError(f"Could not rotate temporary sketch {sketch.name}.")


def _translate_entities(
    sketch: Any,
    entities: List[Any],
    offset_x: float,
    offset_y: float,
) -> None:
    if abs(offset_x) <= 1e-12 and abs(offset_y) <= 1e-12:
        return
    transform = adsk.core.Matrix3D.create()
    transform.translation = adsk.core.Vector3D.create(offset_x, offset_y, 0.0)
    if not sketch.move(_object_collection(entities), transform):
        raise RuntimeError(f"Could not translate temporary sketch {sketch.name}.")


def _object_collection(entities: Iterable[Any]) -> Any:
    collection = adsk.core.ObjectCollection.create()
    for entity in entities:
        collection.add(entity)
    return collection


def _entity_minimum(entities: Iterable[Any]) -> Tuple[float, float]:
    minimum_x, minimum_y, _, _ = _entity_bounds(entities)
    return minimum_x, minimum_y


def _entity_bounds(
    entities: Iterable[Any],
) -> Tuple[float, float, float, float]:
    minimum_x = float("inf")
    minimum_y = float("inf")
    maximum_x = float("-inf")
    maximum_y = float("-inf")
    for entity in entities:
        bounding_box = entity.boundingBox
        minimum_x = min(minimum_x, float(bounding_box.minPoint.x))
        minimum_y = min(minimum_y, float(bounding_box.minPoint.y))
        maximum_x = max(maximum_x, float(bounding_box.maxPoint.x))
        maximum_y = max(maximum_y, float(bounding_box.maxPoint.y))
    if not all(
        math.isfinite(value)
        for value in (minimum_x, minimum_y, maximum_x, maximum_y)
    ):
        raise RuntimeError("Could not calculate the outside-profile bounds.")
    return minimum_x, minimum_y, maximum_x, maximum_y
