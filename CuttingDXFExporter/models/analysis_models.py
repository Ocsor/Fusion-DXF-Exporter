"""Serializable analysis models.

Fusion API wrapper objects are deliberately excluded from these classes so the
same records can later be written to JSON without custom Fusion serializers.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

Vector3 = Tuple[float, float, float]


class OperationType(str, Enum):
    """Manufacturing operation categories supported by the project."""

    CUT_OUTSIDE = "CUT_OUTSIDE"
    CUT_INSIDE = "CUT_INSIDE"
    FRONT_POCKET = "FRONT_POCKET"
    FRONT_REBATE = "FRONT_REBATE"
    MITRE = "MITRE"
    BACK_POCKET = "BACK_POCKET"
    BACK_REBATE = "BACK_REBATE"
    UNKNOWN = "UNKNOWN"


class OperationSide(str, Enum):
    """Side of the part associated with an operation."""

    FRONT = "front"
    BACK = "back"
    THROUGH = "through"


class ConfidenceLevel(str, Enum):
    """Classifier confidence communicated to the operator."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class WarningSeverity(str, Enum):
    """Severity levels for analysis messages."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class AnalysisWarning:
    """A diagnostic generated while analysing geometry."""

    code: str
    message: str
    severity: WarningSeverity = WarningSeverity.WARNING
    requires_review: bool = False


@dataclass
class ManufacturingFace:
    """Serializable description of a proposed manufacturing face."""

    role: str
    face_index: int
    temp_id: Optional[int]
    entity_token: str
    area_internal: float
    area_mm2: float
    origin: Vector3
    normal: Vector3
    x_axis: Vector3
    y_axis: Vector3


@dataclass
class GeometryLoop:
    """A boundary loop and the stable references available for its edges."""

    loop_index: int
    is_outer: bool
    edge_tokens: List[str] = field(default_factory=list)
    edge_temp_ids: List[int] = field(default_factory=list)
    geometry_types: List[str] = field(default_factory=list)
    is_closed: bool = True


@dataclass
class DetectedOperation:
    """A detected or deliberately unresolved manufacturing operation."""

    operation_type: OperationType
    side: OperationSide
    depth_internal: Optional[float]
    depth_mm: Optional[float]
    proposed_layer: str
    angle_degrees: Optional[float] = None
    source_face_tokens: List[str] = field(default_factory=list)
    source_face_temp_ids: List[int] = field(default_factory=list)
    source_edge_tokens: List[str] = field(default_factory=list)
    source_edge_temp_ids: List[int] = field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    warnings: List[AnalysisWarning] = field(default_factory=list)
    is_closed: bool = True
    geometry_summary: str = ""
    operator_approval_required: bool = False
    boundary: Optional[GeometryLoop] = None


@dataclass
class BodyAnalysis:
    """Complete serializable analysis for one selected body."""

    design_name: str
    component_name: str
    body_name: str
    body_token: str
    selection_index: int
    valid_solid: bool
    face_selection_mode: str
    material_name: str = "Unspecified Material"
    planar_face_count: int = 0
    front_face: Optional[ManufacturingFace] = None
    rear_face: Optional[ManufacturingFace] = None
    thickness_internal: Optional[float] = None
    thickness_mm: Optional[float] = None
    constant_thickness: bool = False
    feature_analysis_complete: bool = False
    operations: List[DetectedOperation] = field(default_factory=list)
    warnings: List[AnalysisWarning] = field(default_factory=list)
    operator_review_required: bool = False

    def operation_count(self, operation_type: OperationType) -> int:
        """Return the number of operations of a specific type."""

        return sum(
            1 for operation in self.operations
            if operation.operation_type == operation_type
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe dictionary."""

        return _json_safe(asdict(self))


@dataclass
class ExportResult:
    """Result record shared with later export phases."""

    body_token: str
    output_path: str = ""
    succeeded: bool = False
    warnings: List[AnalysisWarning] = field(default_factory=list)
    error_message: str = ""
    temporary_sketches_cleaned: bool = True
    exported_operation_count: int = 0
    backup_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe dictionary."""

        return _json_safe(asdict(self))


def _json_safe(value: Any) -> Any:
    """Recursively convert enum values and tuples to JSON-safe values."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
