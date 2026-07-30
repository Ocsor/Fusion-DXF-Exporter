"""DXF operation-layer naming helpers."""

from typing import Optional

from ..models.analysis_models import OperationType
from .geometry_utils import format_millimetres

DEPTH_OPERATION_TYPES = {
    OperationType.FRONT_POCKET,
    OperationType.FRONT_REBATE,
    OperationType.BACK_POCKET,
    OperationType.BACK_REBATE,
}


def layer_name_for_operation(
    operation_type: OperationType,
    depth_mm: Optional[float],
    include_depth: bool,
    angle_degrees: Optional[float] = None,
) -> str:
    """Return a stable DXF layer name for one manufacturing operation."""

    if (
        include_depth
        and operation_type in DEPTH_OPERATION_TYPES
        and depth_mm is not None
    ):
        return f"{operation_type.value}_{format_millimetres(depth_mm)}MM"
    if operation_type == OperationType.MITRE and angle_degrees is not None:
        return f"{operation_type.value}_{_format_degrees(angle_degrees)}DEG"
    return operation_type.value


def _format_degrees(angle_degrees: float) -> str:
    rounded = round(angle_degrees, 3)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.3f}".rstrip("0").rstrip(".")
