"""Small geometry helpers independent of Fusion's UI."""

import math
from typing import Any, Iterator, Sequence, Tuple

Vector3 = Tuple[float, float, float]
INTERNAL_LENGTH_TO_MM = 10.0
INTERNAL_AREA_TO_MM2 = 100.0


def iter_fusion_collection(collection: Any) -> Iterator[Any]:
    """Iterate a Fusion collection using its count/item API."""

    for index in range(collection.count):
        yield collection.item(index)


def point_tuple(point: Any) -> Vector3:
    """Convert a Fusion point or vector to a plain tuple."""

    return (float(point.x), float(point.y), float(point.z))


def subtract(first: Sequence[float], second: Sequence[float]) -> Vector3:
    """Subtract two three-dimensional vectors."""

    return (
        float(first[0] - second[0]),
        float(first[1] - second[1]),
        float(first[2] - second[2]),
    )


def dot(first: Sequence[float], second: Sequence[float]) -> float:
    """Return the dot product of two vectors."""

    return float(sum(a * b for a, b in zip(first, second)))


def cross(first: Sequence[float], second: Sequence[float]) -> Vector3:
    """Return the cross product of two vectors."""

    return (
        float(first[1] * second[2] - first[2] * second[1]),
        float(first[2] * second[0] - first[0] * second[2]),
        float(first[0] * second[1] - first[1] * second[0]),
    )


def magnitude(vector: Sequence[float]) -> float:
    """Return vector magnitude."""

    return math.sqrt(dot(vector, vector))


def normalize(vector: Sequence[float]) -> Vector3:
    """Return a unit vector, raising for degenerate input."""

    length = magnitude(vector)
    if length <= 1e-12:
        raise ValueError("Cannot normalize a zero-length vector.")
    return (
        float(vector[0] / length),
        float(vector[1] / length),
        float(vector[2] / length),
    )


def scale(vector: Sequence[float], factor: float) -> Vector3:
    """Scale a vector."""

    return (
        float(vector[0] * factor),
        float(vector[1] * factor),
        float(vector[2] * factor),
    )


def project_onto_plane(vector: Sequence[float], normal: Sequence[float]) -> Vector3:
    """Project a vector onto a plane defined by a unit normal."""

    return subtract(vector, scale(normal, dot(vector, normal)))


def canonical_direction(vector: Sequence[float]) -> Vector3:
    """Choose a stable sign for an otherwise bidirectional axis."""

    normalized = normalize(vector)
    for coordinate in normalized:
        if abs(coordinate) > 1e-9:
            return scale(normalized, -1.0) if coordinate < 0.0 else normalized
    return normalized


def are_parallel(
    first: Sequence[float],
    second: Sequence[float],
    angular_tolerance_radians: float,
) -> bool:
    """Return whether vectors are parallel in either direction."""

    cosine_limit = math.cos(angular_tolerance_radians)
    return abs(dot(normalize(first), normalize(second))) >= cosine_limit


def are_opposed(
    first: Sequence[float],
    second: Sequence[float],
    angular_tolerance_radians: float,
) -> bool:
    """Return whether vectors point in opposite directions."""

    cosine_limit = math.cos(angular_tolerance_radians)
    return dot(normalize(first), normalize(second)) <= -cosine_limit


def signed_plane_offset(
    origin: Sequence[float],
    point: Sequence[float],
    normal: Sequence[float],
) -> float:
    """Measure point offset from a plane along its unit normal."""

    return dot(subtract(point, origin), normalize(normal))


def internal_length_to_mm(value: float) -> float:
    """Convert Fusion's centimetre length units to millimetres."""

    return float(value * INTERNAL_LENGTH_TO_MM)


def internal_area_to_mm2(value: float) -> float:
    """Convert Fusion's square-centimetre area units to square millimetres."""

    return float(value * INTERNAL_AREA_TO_MM2)


def format_millimetres(value: float) -> str:
    """Format millimetres compactly with at most three decimal places."""

    rounded = round(float(value), 3)
    if abs(rounded - round(rounded)) <= 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.3f}".rstrip("0").rstrip(".")
