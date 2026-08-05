"""Safe ASCII DXF layer assignment and category-file merging."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

GroupPair = Tuple[int, str]
LAYER_COLORS = {
    "CUT_OUTSIDE": 1,
    "CUT_INSIDE": 5,
    "FRONT_POCKET": 3,
    "FRONT_REBATE": 4,
    "MITRE": 6,
    "REVERSE_MITRE": 6,
    "BACK_POCKET": 2,
    "BACK_REBATE": 6,
    "UNKNOWN": 8,
    "markups": 2,
}
MARKUP_LAYER = "markups"
CUT_DEDUPLICATION_TOLERANCE = 1e-5


@dataclass
class DxfMergeResult:
    """Validated result of an atomic category-DXF merge."""

    output_path: str
    entity_counts: Dict[str, int] = field(default_factory=dict)
    layer_names: List[str] = field(default_factory=list)


def read_ascii_group_pairs(path: str) -> List[GroupPair]:
    """Read an ASCII DXF as validated group-code/value pairs."""

    data = Path(path).read_bytes()
    if data.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("Binary DXF is not supported.")
    if b"\x00" in data:
        raise ValueError("The DXF contains binary data and cannot be processed.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError as error:
            raise ValueError("The DXF is not a supported ASCII text file.") from error
    lines = text.splitlines()
    if len(lines) % 2:
        raise ValueError("The DXF contains an unmatched group code/value line.")
    pairs: List[GroupPair] = []
    for index in range(0, len(lines), 2):
        try:
            group_code = int(lines[index].strip())
        except ValueError as error:
            raise ValueError(
                f"Invalid DXF group code at line {index + 1}."
            ) from error
        pairs.append((group_code, lines[index + 1]))
    if not any(code == 0 and value.strip() == "EOF" for code, value in pairs):
        raise ValueError("The DXF does not contain an EOF marker.")
    return pairs


def write_ascii_group_pairs(path: str, pairs: Iterable[GroupPair]) -> None:
    """Write normalized ASCII DXF group pairs using Windows line endings."""

    with Path(path).open("w", encoding="cp1252", newline="") as stream:
        for code, value in pairs:
            stream.write(f"{code:>3}\r\n{value}\r\n")


def merge_category_dxfs(
    category_paths: Dict[str, str],
    output_path: str,
    markup_text: Optional[str] = None,
) -> DxfMergeResult:
    """Merge category DXFs, assign layers, validate, then atomically publish."""

    if "CUT_OUTSIDE" not in category_paths:
        raise ValueError("CUT_OUTSIDE DXF is required as the merge base.")

    base_pairs = read_ascii_group_pairs(category_paths["CUT_OUTSIDE"])
    next_handle = [_next_available_handle(base_pairs)]
    merged_entities: List[GroupPair] = []
    expected_counts: Dict[str, int] = {}
    for layer_name, category_path in category_paths.items():
        category_pairs = read_ascii_group_pairs(category_path)
        entity_pairs = _section_content(category_pairs, "ENTITIES")
        layered_entities, entity_count = _assign_entities_to_layer(
            entity_pairs,
            layer_name,
        )
        layered_entities = _remap_entity_handles(
            layered_entities,
            next_handle,
        )
        if entity_count < 1:
            raise ValueError(f"{layer_name} DXF contains no exportable entities.")
        merged_entities.extend(layered_entities)
        expected_counts[layer_name] = entity_count

    merged_entities, removed_counts = _deduplicate_cut_entities(merged_entities)
    for layer_name, removed_count in removed_counts.items():
        expected_counts[layer_name] -= removed_count

    required_layers = [
        layer_name
        for layer_name in category_paths
        if expected_counts.get(layer_name, 0) > 0
    ]
    if markup_text:
        bounds = _layer_entity_bounds(merged_entities, "CUT_OUTSIDE")
        if not bounds:
            raise ValueError("CUT_OUTSIDE bounds are unavailable for filename markup.")
        minimum_x, minimum_y, maximum_x, maximum_y = bounds
        centre = (
            (minimum_x + maximum_x) / 2.0,
            (minimum_y + maximum_y) / 2.0,
        )
        shorter_side = min(maximum_x - minimum_x, maximum_y - minimum_y)
        text_height = max(1.0, min(10.0, shorter_side * 0.05))
        merged_entities.extend(
            _text_entity(
                markup_text,
                MARKUP_LAYER,
                centre,
                text_height,
                _allocate_handle(next_handle),
                _uses_object_subclasses(base_pairs),
            )
        )
        required_layers.append(MARKUP_LAYER)
        expected_counts[MARKUP_LAYER] = 1

    working_pairs = _ensure_layers(
        base_pairs,
        required_layers,
        next_handle,
    )
    working_pairs = _replace_section_content(
        working_pairs,
        "ENTITIES",
        merged_entities,
    )
    working_pairs = _set_handseed(working_pairs, next_handle[0])

    temporary_path = f"{output_path}.postprocess.tmp"
    try:
        write_ascii_group_pairs(temporary_path, working_pairs)
        validation = validate_layered_dxf(
            temporary_path,
            required_layers,
        )
        for layer_name, expected_count in expected_counts.items():
            if validation.entity_counts.get(layer_name, 0) != expected_count:
                raise ValueError(
                    f"Layer {layer_name} entity count changed during validation."
                )
        os.replace(temporary_path, output_path)
        validation.output_path = output_path
        return validation
    except Exception:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
        raise


def merge_body_category_dxfs(
    body_category_paths: Sequence[Dict[str, str]],
    output_path: str,
    markup_text: Optional[str] = None,
    body_spacing: float = 10.0,
    preserve_body_positions: bool = False,
) -> DxfMergeResult:
    """Merge multiple bodies into one layered DXF."""

    if not body_category_paths:
        raise ValueError("At least one body DXF set is required.")
    for category_paths in body_category_paths:
        if "CUT_OUTSIDE" not in category_paths:
            raise ValueError("Every body requires a CUT_OUTSIDE DXF.")

    base_pairs = read_ascii_group_pairs(
        body_category_paths[0]["CUT_OUTSIDE"]
    )
    next_handle = [_next_available_handle(base_pairs)]
    merged_entities: List[GroupPair] = []
    expected_counts: Dict[str, int] = {}
    required_layers: List[str] = []
    next_body_x = 0.0

    for category_paths in body_category_paths:
        outside_pairs = read_ascii_group_pairs(category_paths["CUT_OUTSIDE"])
        outside_entities = _section_content(outside_pairs, "ENTITIES")
        bounds = _entity_bounds(outside_entities)
        if not bounds:
            raise ValueError("CUT_OUTSIDE bounds are unavailable for body placement.")
        minimum_x, minimum_y, maximum_x, _ = bounds
        if preserve_body_positions:
            offset_x = 0.0
            offset_y = 0.0
        else:
            offset_x = next_body_x - minimum_x
            offset_y = -minimum_y
            next_body_x += maximum_x - minimum_x + body_spacing

        for layer_name, category_path in category_paths.items():
            category_pairs = read_ascii_group_pairs(category_path)
            entity_pairs = _section_content(category_pairs, "ENTITIES")
            layered_entities, entity_count = _assign_entities_to_layer(
                entity_pairs,
                layer_name,
            )
            if entity_count < 1:
                raise ValueError(
                    f"{layer_name} DXF contains no exportable entities."
                )
            layered_entities = _translate_entity_pairs(
                layered_entities,
                offset_x,
                offset_y,
            )
            layered_entities = _remap_entity_handles(
                layered_entities,
                next_handle,
            )
            merged_entities.extend(layered_entities)
            expected_counts[layer_name] = (
                expected_counts.get(layer_name, 0) + entity_count
            )
            if layer_name not in required_layers:
                required_layers.append(layer_name)

    merged_entities, removed_counts = _deduplicate_cut_entities(merged_entities)
    for layer_name, removed_count in removed_counts.items():
        expected_counts[layer_name] -= removed_count
    required_layers = [
        layer_name
        for layer_name in required_layers
        if expected_counts.get(layer_name, 0) > 0
    ]

    if preserve_body_positions:
        bounds = _layer_entity_bounds(merged_entities, "CUT_OUTSIDE")
        if not bounds:
            raise ValueError("CUT_OUTSIDE bounds are unavailable for merged placement.")
        minimum_x, minimum_y, _, _ = bounds
        merged_entities = _translate_entity_pairs(
            merged_entities,
            -minimum_x,
            -minimum_y,
        )

    if markup_text:
        bounds = _layer_entity_bounds(merged_entities, "CUT_OUTSIDE")
        if not bounds:
            raise ValueError("CUT_OUTSIDE bounds are unavailable for filename markup.")
        minimum_x, minimum_y, maximum_x, maximum_y = bounds
        centre = (
            (minimum_x + maximum_x) / 2.0,
            (minimum_y + maximum_y) / 2.0,
        )
        shorter_side = min(maximum_x - minimum_x, maximum_y - minimum_y)
        text_height = max(1.0, min(10.0, shorter_side * 0.05))
        merged_entities.extend(
            _text_entity(
                markup_text,
                MARKUP_LAYER,
                centre,
                text_height,
                _allocate_handle(next_handle),
                _uses_object_subclasses(base_pairs),
            )
        )
        required_layers.append(MARKUP_LAYER)
        expected_counts[MARKUP_LAYER] = 1

    working_pairs = _ensure_layers(
        base_pairs,
        required_layers,
        next_handle,
    )
    working_pairs = _replace_section_content(
        working_pairs,
        "ENTITIES",
        merged_entities,
    )
    working_pairs = _set_handseed(working_pairs, next_handle[0])

    temporary_path = f"{output_path}.postprocess.tmp"
    try:
        write_ascii_group_pairs(temporary_path, working_pairs)
        validation = validate_layered_dxf(
            temporary_path,
            required_layers,
        )
        for layer_name, expected_count in expected_counts.items():
            if validation.entity_counts.get(layer_name, 0) != expected_count:
                raise ValueError(
                    f"Layer {layer_name} entity count changed during validation."
                )
        os.replace(temporary_path, output_path)
        validation.output_path = output_path
        return validation
    except Exception:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
        raise


def validate_layered_dxf(
    path: str,
    required_layers: Sequence[str],
) -> DxfMergeResult:
    """Validate layer definitions and count entities assigned to each layer."""

    pairs = read_ascii_group_pairs(path)
    layer_names = _defined_layer_names(pairs)
    missing_layers = [
        layer_name for layer_name in required_layers
        if layer_name not in layer_names
    ]
    if missing_layers:
        raise ValueError(
            f"DXF is missing required layers: {', '.join(missing_layers)}"
        )
    _validate_required_layer_records(pairs, required_layers)
    _validate_unique_object_handles(pairs)

    entity_counts = {layer_name: 0 for layer_name in required_layers}
    for record in _entity_records(_section_content(pairs, "ENTITIES")):
        entity_type = record[0][1].strip().upper() if record else ""
        if entity_type in {"SEQEND", "ENDSEC"}:
            continue
        layer = next(
            (value.strip() for code, value in record if code == 8),
            "",
        )
        if layer in entity_counts:
            entity_counts[layer] += 1
    empty_layers = [
        layer_name for layer_name, count in entity_counts.items()
        if count < 1
    ]
    if empty_layers:
        raise ValueError(
            f"DXF layers contain no entities: {', '.join(empty_layers)}"
        )
    return DxfMergeResult(
        output_path=path,
        entity_counts=entity_counts,
        layer_names=sorted(layer_names),
    )


def _section_bounds(
    pairs: Sequence[GroupPair],
    section_name: str,
) -> Tuple[int, int]:
    target = section_name.upper()
    for index in range(len(pairs) - 1):
        if (
            pairs[index][0] == 0
            and pairs[index][1].strip().upper() == "SECTION"
            and pairs[index + 1][0] == 2
            and pairs[index + 1][1].strip().upper() == target
        ):
            for end_index in range(index + 2, len(pairs)):
                if (
                    pairs[end_index][0] == 0
                    and pairs[end_index][1].strip().upper() == "ENDSEC"
                ):
                    return index + 2, end_index
            break
    raise ValueError(f"DXF section {section_name} was not found or is incomplete.")


def _section_content(
    pairs: Sequence[GroupPair],
    section_name: str,
) -> List[GroupPair]:
    start, end = _section_bounds(pairs, section_name)
    return list(pairs[start:end])


def _replace_section_content(
    pairs: Sequence[GroupPair],
    section_name: str,
    content: Sequence[GroupPair],
) -> List[GroupPair]:
    start, end = _section_bounds(pairs, section_name)
    return list(pairs[:start]) + list(content) + list(pairs[end:])


def _entity_records(pairs: Sequence[GroupPair]) -> List[List[GroupPair]]:
    records: List[List[GroupPair]] = []
    current: List[GroupPair] = []
    for pair in pairs:
        if pair[0] == 0:
            if current:
                records.append(current)
            current = [pair]
        elif current:
            current.append(pair)
    if current:
        records.append(current)
    return records


def _assign_entities_to_layer(
    pairs: Sequence[GroupPair],
    layer_name: str,
) -> Tuple[List[GroupPair], int]:
    output: List[GroupPair] = []
    entity_count = 0
    for record in _entity_records(pairs):
        entity_type = record[0][1].strip().upper()
        if entity_type == "SEQEND":
            output.extend(_set_record_layer(record, layer_name))
            continue
        output.extend(_set_record_layer(record, layer_name))
        entity_count += 1
    return output, entity_count


def _set_record_layer(
    record: Sequence[GroupPair],
    layer_name: str,
) -> List[GroupPair]:
    updated = list(record)
    for index, pair in enumerate(updated):
        if pair[0] == 8:
            updated[index] = (8, layer_name)
            return updated
    return [updated[0], (8, layer_name)] + updated[1:]


def _deduplicate_cut_entities(
    pairs: Sequence[GroupPair],
) -> Tuple[List[GroupPair], Dict[str, int]]:
    cut_layers = {"CUT_OUTSIDE", "CUT_INSIDE"}
    records = _entity_records(pairs)
    outside_signatures = {
        signature
        for record in records
        if _record_layer(record) == "CUT_OUTSIDE"
        and (signature := _entity_geometry_signature(record)) is not None
    }
    seen_signatures = {layer_name: set() for layer_name in cut_layers}
    removed_counts: Dict[str, int] = {}
    deduplicated: List[GroupPair] = []
    for record in records:
        layer_name = _record_layer(record)
        if layer_name not in cut_layers:
            deduplicated.extend(record)
            continue
        signature = _entity_geometry_signature(record)
        duplicate = bool(
            signature is not None
            and (
                signature in seen_signatures[layer_name]
                or (
                    layer_name == "CUT_INSIDE"
                    and signature in outside_signatures
                )
            )
        )
        if duplicate:
            removed_counts[layer_name] = removed_counts.get(layer_name, 0) + 1
            continue
        if signature is not None:
            seen_signatures[layer_name].add(signature)
        deduplicated.extend(record)
    return deduplicated, removed_counts


def _record_layer(record: Sequence[GroupPair]) -> str:
    return next(
        (value.strip() for code, value in record if code == 8),
        "",
    )


def _entity_geometry_signature(
    record: Sequence[GroupPair],
) -> Optional[Tuple[Any, ...]]:
    if not record:
        return None
    entity_type = record[0][1].strip().upper()
    ignored_codes = {5, 6, 8, 48, 62, 100, 330, 370, 410}
    geometry = tuple(
        (code, _canonical_geometry_value(value))
        for code, value in record[1:]
        if code not in ignored_codes
    )
    if entity_type == "LINE":
        start = _record_point(record, 10)
        end = _record_point(record, 11)
        if start is not None and end is not None:
            return entity_type, tuple(sorted((start, end)))
    return entity_type, geometry


def _record_point(
    record: Sequence[GroupPair],
    x_code: int,
) -> Optional[Tuple[int, int, int]]:
    coordinates = []
    for code in (x_code, x_code + 10, x_code + 20):
        value = next((item for item_code, item in record if item_code == code), "0")
        try:
            coordinates.append(_quantized_geometry_number(float(value.strip())))
        except ValueError:
            return None
    return tuple(coordinates)


def _canonical_geometry_value(value: str) -> Any:
    stripped = value.strip()
    try:
        return _quantized_geometry_number(float(stripped))
    except ValueError:
        return stripped.upper()


def _quantized_geometry_number(value: float) -> int:
    return int(round(value / CUT_DEDUPLICATION_TOLERANCE))


def _layer_entity_bounds(
    pairs: Sequence[GroupPair],
    layer_name: str,
) -> Optional[Tuple[float, float, float, float]]:
    bounds = []
    for record in _entity_records(pairs):
        record_layer = next(
            (value.strip() for code, value in record if code == 8),
            "",
        )
        if record_layer != layer_name:
            continue
        record_bounds = _record_bounds(record)
        if record_bounds:
            bounds.append(record_bounds)
    if not bounds:
        return None
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _entity_bounds(
    pairs: Sequence[GroupPair],
) -> Optional[Tuple[float, float, float, float]]:
    bounds = [
        record_bounds
        for record in _entity_records(pairs)
        if (record_bounds := _record_bounds(record)) is not None
    ]
    if not bounds:
        return None
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _record_bounds(
    record: Sequence[GroupPair],
) -> Optional[Tuple[float, float, float, float]]:
    entity_type = record[0][1].strip().upper()
    point_codes = (
        (10, 11)
        if entity_type in {"LINE", "SPLINE", "TEXT"}
        else (10,)
    )
    points = []
    for x_code in point_codes:
        x_values = _numeric_group_values(record, {x_code})
        y_values = _numeric_group_values(record, {x_code + 10})
        points.extend(zip(x_values, y_values))
    if not points:
        return None
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    if entity_type in {"CIRCLE", "ARC"}:
        radius_values = _numeric_group_values(record, {40})
        if radius_values:
            radius = abs(radius_values[0])
            x_values.extend((points[0][0] - radius, points[0][0] + radius))
            y_values.extend((points[0][1] - radius, points[0][1] + radius))
    elif entity_type == "ELLIPSE":
        major_x = _numeric_group_values(record, {11})
        major_y = _numeric_group_values(record, {21})
        ratios = _numeric_group_values(record, {40})
        if major_x and major_y and ratios:
            ratio = abs(ratios[0])
            x_extent = math.sqrt(
                major_x[0] ** 2 + (major_y[0] * ratio) ** 2
            )
            y_extent = math.sqrt(
                major_y[0] ** 2 + (major_x[0] * ratio) ** 2
            )
            x_values.extend(
                (points[0][0] - x_extent, points[0][0] + x_extent)
            )
            y_values.extend(
                (points[0][1] - y_extent, points[0][1] + y_extent)
            )
    return min(x_values), min(y_values), max(x_values), max(y_values)


def _translate_entity_pairs(
    pairs: Sequence[GroupPair],
    offset_x: float,
    offset_y: float,
) -> List[GroupPair]:
    translated = []
    for record in _entity_records(pairs):
        entity_type = record[0][1].strip().upper()
        x_codes = (
            {10, 11}
            if entity_type in {"LINE", "SPLINE", "TEXT"}
            else {10}
        )
        y_codes = {code + 10 for code in x_codes}
        for code, value in record:
            offset = (
                offset_x
                if code in x_codes
                else offset_y
                if code in y_codes
                else None
            )
            if offset is None:
                translated.append((code, value))
                continue
            try:
                translated.append(
                    (code, _dxf_number(float(value.strip()) + offset))
                )
            except ValueError:
                translated.append((code, value))
    return translated


def _numeric_group_values(
    record: Sequence[GroupPair],
    group_codes: Iterable[int],
) -> List[float]:
    accepted_codes = set(group_codes)
    values = []
    for code, value in record:
        if code not in accepted_codes:
            continue
        try:
            values.append(float(value.strip()))
        except ValueError:
            continue
    return values


def _text_entity(
    text: str,
    layer_name: str,
    centre: Tuple[float, float],
    height: float,
    handle: str,
    include_subclasses: bool,
) -> List[GroupPair]:
    safe_text = (
        text.replace("\r", " ")
        .replace("\n", " ")
        .encode("cp1252", errors="replace")
        .decode("cp1252")
    )
    centre_x = _dxf_number(centre[0])
    centre_y = _dxf_number(centre[1])
    record: List[GroupPair] = [
        (0, "TEXT"),
        (5, handle),
    ]
    if include_subclasses:
        record.append((100, "AcDbEntity"))
    record.append((8, layer_name))
    if include_subclasses:
        record.append((100, "AcDbText"))
    record.extend(
        [
            (10, centre_x),
            (20, centre_y),
            (30, "0"),
            (40, _dxf_number(height)),
            (1, safe_text),
            (50, "0"),
            (72, "1"),
            (11, centre_x),
            (21, centre_y),
            (31, "0"),
        ]
    )
    if include_subclasses:
        record.append((100, "AcDbText"))
    record.append((73, "2"))
    return record


def _dxf_number(value: float) -> str:
    return f"{value:.9f}".rstrip("0").rstrip(".") or "0"


def _ensure_layers(
    pairs: Sequence[GroupPair],
    required_layers: Sequence[str],
    next_handle: List[int],
) -> List[GroupPair]:
    tables_start, tables_end = _section_bounds(pairs, "TABLES")
    working = list(pairs)
    layer_table = _find_table_bounds(working, tables_start, tables_end, "LAYER")
    if layer_table:
        table_start, table_end = layer_table
        existing = _layer_names_in_table(working[table_start:table_end])
        missing = [name for name in required_layers if name not in existing]
        if not missing:
            return working
        header_end = _first_table_record(working, table_start + 2, table_end)
        existing_count = len(existing)
        count_updated = False
        for index in range(table_start + 2, header_end):
            if working[index][0] == 70:
                working[index] = (70, str(existing_count + len(missing)))
                count_updated = True
                break
        if not count_updated:
            working.insert(header_end, (70, str(existing_count + len(missing))))
            table_end += 1
        insertion = []
        for layer_name in missing:
            insertion.extend(
                _layer_record(
                    layer_name,
                    _allocate_handle(next_handle),
                    _uses_object_subclasses(working),
                )
            )
        return working[:table_end] + insertion + working[table_end:]

    table_pairs: List[GroupPair] = [
        (0, "TABLE"),
        (2, "LAYER"),
    ]
    if _uses_object_subclasses(working):
        table_pairs.extend(
            [
                (5, _allocate_handle(next_handle)),
                (100, "AcDbSymbolTable"),
            ]
        )
    table_pairs.extend(
        [
            (70, str(len(required_layers))),
        ]
    )
    for layer_name in required_layers:
        table_pairs.extend(
            _layer_record(
                layer_name,
                _allocate_handle(next_handle),
                _uses_object_subclasses(working),
            )
        )
    table_pairs.append((0, "ENDTAB"))
    return working[:tables_end] + table_pairs + working[tables_end:]


def _find_table_bounds(
    pairs: Sequence[GroupPair],
    start: int,
    end: int,
    table_name: str,
) -> Optional[Tuple[int, int]]:
    target = table_name.upper()
    for index in range(start, end - 1):
        if (
            pairs[index][0] == 0
            and pairs[index][1].strip().upper() == "TABLE"
            and pairs[index + 1][0] == 2
            and pairs[index + 1][1].strip().upper() == target
        ):
            for end_index in range(index + 2, end):
                if (
                    pairs[end_index][0] == 0
                    and pairs[end_index][1].strip().upper() == "ENDTAB"
                ):
                    return index, end_index
    return None


def _first_table_record(
    pairs: Sequence[GroupPair],
    start: int,
    end: int,
) -> int:
    for index in range(start, end):
        if pairs[index][0] == 0:
            return index
    return end


def _layer_names_in_table(pairs: Sequence[GroupPair]) -> List[str]:
    names: List[str] = []
    records = _entity_records(pairs)
    for record in records:
        if record and record[0][1].strip().upper() == "LAYER":
            name = next(
                (value.strip() for code, value in record if code == 2),
                "",
            )
            if name:
                names.append(name)
    return names


def _defined_layer_names(pairs: Sequence[GroupPair]) -> List[str]:
    tables_start, tables_end = _section_bounds(pairs, "TABLES")
    bounds = _find_table_bounds(
        pairs,
        tables_start,
        tables_end,
        "LAYER",
    )
    if not bounds:
        return []
    start, end = bounds
    return _layer_names_in_table(pairs[start:end])


def _layer_record(
    layer_name: str,
    handle: str,
    include_subclasses: bool,
) -> List[GroupPair]:
    record = [
        (0, "LAYER"),
        (5, handle),
    ]
    if include_subclasses:
        record.extend(
            [
                (100, "AcDbSymbolTableRecord"),
                (100, "AcDbLayerTableRecord"),
            ]
        )
    record.extend(
        [
            (2, layer_name),
            (70, "0"),
            (62, str(_layer_color(layer_name))),
            (6, "CONTINUOUS"),
        ]
    )
    return record


def _layer_color(layer_name: str) -> int:
    for prefix, color in LAYER_COLORS.items():
        if layer_name == prefix or layer_name.startswith(f"{prefix}_"):
            return color
    return 7


def _next_available_handle(pairs: Sequence[GroupPair]) -> int:
    handles = [
        int(value.strip(), 16)
        for code, value in pairs
        if code == 5 and _is_hex_handle(value)
    ]
    return max(handles, default=0) + 1


def _allocate_handle(next_handle: List[int]) -> str:
    handle = f"{next_handle[0]:X}"
    next_handle[0] += 1
    return handle


def _remap_entity_handles(
    pairs: Sequence[GroupPair],
    next_handle: List[int],
) -> List[GroupPair]:
    records = _entity_records(pairs)
    handle_map: Dict[str, str] = {}
    for record in records:
        old_handle = next(
            (value.strip().upper() for code, value in record if code == 5),
            "",
        )
        if old_handle:
            handle_map[old_handle] = _allocate_handle(next_handle)

    remapped: List[GroupPair] = []
    for record in records:
        old_handle = next(
            (value.strip().upper() for code, value in record if code == 5),
            "",
        )
        new_handle = handle_map.get(old_handle) or _allocate_handle(next_handle)
        has_handle = False
        for code, value in record:
            normalized = value.strip().upper()
            if code == 5:
                remapped.append((5, new_handle))
                has_handle = True
            elif 320 <= code <= 369 and normalized in handle_map:
                remapped.append((code, handle_map[normalized]))
            elif 390 <= code <= 399 and normalized in handle_map:
                remapped.append((code, handle_map[normalized]))
            else:
                remapped.append((code, value))
        if not has_handle:
            record_start = len(remapped) - len(record)
            remapped.insert(record_start + 1, (5, new_handle))
    return remapped


def _set_handseed(
    pairs: Sequence[GroupPair],
    next_handle: int,
) -> List[GroupPair]:
    working = list(pairs)
    for index, pair in enumerate(working[:-1]):
        if pair[0] == 9 and pair[1].strip().upper() == "$HANDSEED":
            if working[index + 1][0] != 5:
                raise ValueError("$HANDSEED is not followed by a handle value.")
            working[index + 1] = (5, f"{next_handle:X}")
            return working
    raise ValueError("The DXF HEADER does not contain $HANDSEED.")


def _uses_object_subclasses(pairs: Sequence[GroupPair]) -> bool:
    for index, pair in enumerate(pairs[:-1]):
        if pair[0] == 9 and pair[1].strip().upper() == "$ACADVER":
            version = pairs[index + 1][1].strip().upper()
            if version.startswith("AC") and version[2:].isdigit():
                return int(version[2:]) >= 1012
    return False


def _validate_required_layer_records(
    pairs: Sequence[GroupPair],
    required_layers: Sequence[str],
) -> None:
    tables_start, tables_end = _section_bounds(pairs, "TABLES")
    bounds = _find_table_bounds(pairs, tables_start, tables_end, "LAYER")
    if not bounds:
        raise ValueError("DXF does not contain a LAYER table.")
    start, end = bounds
    records = {
        next(
            (value.strip() for code, value in record if code == 2),
            "",
        ): record
        for record in _entity_records(pairs[start:end])
        if record and record[0][1].strip().upper() == "LAYER"
    }
    for layer_name in required_layers:
        record = records.get(layer_name)
        if not record:
            raise ValueError(f"DXF layer record {layer_name} is missing.")
        if not _uses_object_subclasses(pairs):
            continue
        handle = next(
            (value.strip() for code, value in record if code == 5),
            "",
        )
        subclasses = [
            value.strip()
            for code, value in record
            if code == 100
        ]
        if not _is_hex_handle(handle):
            raise ValueError(f"DXF layer {layer_name} has no valid handle.")
        if (
            "AcDbSymbolTableRecord" not in subclasses
            or "AcDbLayerTableRecord" not in subclasses
        ):
            raise ValueError(
                f"DXF layer {layer_name} is missing R13+ subclass markers."
            )


def _validate_unique_object_handles(pairs: Sequence[GroupPair]) -> None:
    seen = set()
    duplicates = set()
    for record in _entity_records(pairs):
        if not record or record[0][0] != 0:
            continue
        handle = next(
            (value.strip().upper() for code, value in record if code == 5),
            "",
        )
        if not handle:
            continue
        if handle in seen:
            duplicates.add(handle)
        seen.add(handle)
    if duplicates:
        raise ValueError(
            "DXF contains duplicate object handles: "
            + ", ".join(sorted(duplicates))
        )


def _is_hex_handle(value: str) -> bool:
    candidate = value.strip()
    return bool(candidate) and all(
        character in "0123456789abcdefABCDEF"
        for character in candidate
    )
