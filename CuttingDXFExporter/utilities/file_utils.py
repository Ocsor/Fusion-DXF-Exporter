"""Windows-safe file and folder helpers."""

import os
import re
from pathlib import Path

from .geometry_utils import format_millimetres

INVALID_WINDOWS_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def default_output_folder() -> str:
    """Return a practical default folder without creating it."""

    documents = Path.home() / "Documents"
    return str(documents if documents.is_dir() else Path.home())


def validate_output_folder(path: str) -> str:
    """Return a normalized existing writable folder or raise ValueError."""

    candidate = Path(path).expanduser()
    if not candidate.is_dir():
        raise ValueError(f"Output folder does not exist: {candidate}")
    if not os.access(str(candidate), os.W_OK):
        raise ValueError(f"Output folder is not writable: {candidate}")
    return str(candidate.resolve())


def sanitize_windows_filename(value: str, fallback: str = "Unnamed") -> str:
    """Sanitize one filename segment for Windows."""

    sanitized = INVALID_WINDOWS_FILENAME.sub("_", value).strip().rstrip(". ")
    if not sanitized:
        sanitized = fallback
    if sanitized.upper() in RESERVED_WINDOWS_NAMES:
        sanitized = f"_{sanitized}"
    return sanitized


def unique_path(path: str) -> str:
    """Add a numeric suffix when a path already exists."""

    candidate = Path(path)
    if not candidate.exists():
        return str(candidate)
    for index in range(2, 10000):
        numbered = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
        if not numbered.exists():
            return str(numbered)
    raise RuntimeError(f"Unable to find an available filename for {candidate}.")


def render_body_filename(
    template: str,
    component_name: str,
    body_name: str,
) -> str:
    """Render the supported filename tokens and sanitize the result."""

    try:
        rendered = template.format(
            component=sanitize_windows_filename(component_name),
            body=sanitize_windows_filename(body_name),
        )
    except (KeyError, IndexError, ValueError) as error:
        raise ValueError(
            "Filename format supports only {component} and {body}."
        ) from error
    return sanitize_windows_filename(rendered, fallback="CuttingPart")


def append_thickness_suffix(filename: str, thickness_mm: float) -> str:
    """Append a compact material-thickness suffix to a filename stem."""

    thickness = format_millimetres(thickness_mm)
    return f"{filename}_{thickness}mm"


def material_output_folder(output_folder: str, material_name: str) -> str:
    """Return a safe one-level physical-material subfolder path."""

    folder_name = sanitize_windows_filename(
        material_name,
        fallback="Unspecified Material",
    )
    return os.path.join(output_folder, folder_name)


def design_output_folder(output_folder: str, design_name: str) -> str:
    """Return a safe one-level Fusion-design subfolder path."""

    folder_name = sanitize_windows_filename(
        design_name,
        fallback="Untitled Design",
    )
    return os.path.join(output_folder, folder_name)
