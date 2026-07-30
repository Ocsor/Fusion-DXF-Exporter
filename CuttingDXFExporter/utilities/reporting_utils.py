"""Atomic CSV and JSON analysis-report writing."""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable

from ..models.analysis_models import BodyAnalysis, ExportResult
from .layer_utils import layer_name_for_operation

CSV_FILENAME = "cutting_dxf_analysis.csv"
JSON_FILENAME = "cutting_dxf_analysis.json"


def write_analysis_csv(
    output_folder: str,
    analyses: Iterable[BodyAnalysis],
    export_results: Iterable[ExportResult],
    include_depth_in_layer_names: bool,
) -> str:
    """Write one CSV row per detected operation and atomically publish it."""

    path = os.path.join(output_folder, CSV_FILENAME)
    temporary_path = f"{path}.tmp"
    result_by_body = _results_by_body(export_results)
    export_time = datetime.now(timezone.utc).isoformat()
    fieldnames = [
        "design_name",
        "component_name",
        "body_name",
        "material_name",
        "operation_type",
        "side",
        "depth_mm",
        "angle_degrees",
        "material_thickness_mm",
        "layer_name",
        "confidence",
        "warning",
        "output_dxf",
        "export_date_utc",
    ]
    try:
        with Path(temporary_path).open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            for analysis in analyses:
                result = result_by_body.get(analysis.body_token)
                for operation in analysis.operations:
                    writer.writerow(
                        {
                            "design_name": analysis.design_name,
                            "component_name": analysis.component_name,
                            "body_name": analysis.body_name,
                            "material_name": analysis.material_name,
                            "operation_type": operation.operation_type.value,
                            "side": operation.side.value,
                            "depth_mm": _number(operation.depth_mm),
                            "angle_degrees": _number(operation.angle_degrees),
                            "material_thickness_mm": _number(
                                analysis.thickness_mm
                            ),
                            "layer_name": layer_name_for_operation(
                                operation.operation_type,
                                operation.depth_mm,
                                include_depth_in_layer_names,
                                angle_degrees=operation.angle_degrees,
                            ),
                            "confidence": operation.confidence.value,
                            "warning": " | ".join(
                                warning.message
                                for warning in operation.warnings
                            ),
                            "output_dxf": (
                                result.output_path
                                if result and result.succeeded
                                else ""
                            ),
                            "export_date_utc": export_time,
                        }
                    )
        os.replace(temporary_path, path)
        return path
    except Exception:
        _remove_if_present(temporary_path)
        raise


def write_analysis_json(
    output_folder: str,
    analyses: Iterable[BodyAnalysis],
    export_results: Iterable[ExportResult],
    addin_version: str,
    include_depth_in_layer_names: bool,
) -> str:
    """Write complete structured analysis and export results atomically."""

    path = os.path.join(output_folder, JSON_FILENAME)
    temporary_path = f"{path}.tmp"
    document = {
        "addin_version": addin_version,
        "export_date_utc": datetime.now(timezone.utc).isoformat(),
        "phase": 3,
        "include_depth_in_layer_names": include_depth_in_layer_names,
        "rear_geometry_convention": (
            "Reserved for Phase 4; rear machining is not exported in Phase 3."
        ),
        "analyses": [analysis.to_dict() for analysis in analyses],
        "export_results": [result.to_dict() for result in export_results],
    }
    try:
        with Path(temporary_path).open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_path, path)
        return path
    except Exception:
        _remove_if_present(temporary_path)
        raise


def _results_by_body(
    export_results: Iterable[ExportResult],
) -> Dict[str, ExportResult]:
    return {result.body_token: result for result in export_results}


def _number(value) -> str:
    return "" if value is None else f"{float(value):.6f}".rstrip("0").rstrip(".")


def _remove_if_present(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
