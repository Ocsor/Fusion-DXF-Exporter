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
    OperationType.MITRE,
)
DEFAULT_MITRE_OFFSET_INTERNAL = 0.05
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
    include_depth_in_layer_names: bool,
    mitre_offset_internal: float = DEFAULT_MITRE_OFFSET_INTERNAL,
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
        layer_name = layer_name_for_operation(
            operation.operation_type,
            operation.depth_mm,
            include_depth_in_layer_names,
            angle_degrees=operation.angle_degrees,
        )
        grouped.setdefault(layer_name, []).append(operation)
    return grouped


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
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    if mitre_offset_internal < 0.0:
        raise ValueError("The mitre guide offset cannot be negative.")
    records = []
    centre_x, centre_y = outside_centre
    for start, end in source_lines:
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
