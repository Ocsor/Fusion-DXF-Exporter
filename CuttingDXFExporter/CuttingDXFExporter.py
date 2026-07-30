"""Autodesk Fusion add-in entry points."""

import importlib
import os
import sys
import tempfile
import traceback
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import adsk.core

RUNTIME_PACKAGE = "_cutting_dxf_exporter_runtime"
STARTUP_LOG = Path(tempfile.gettempdir()) / "CuttingDXFExporter_startup.log"
_command_module: Optional[Any] = None


def run(context) -> None:
    """Load the Cutting DXF Exporter UI."""

    global _command_module
    try:
        _command_module = _load_command_module()
        _command_module.start()
    except Exception:
        failure = traceback.format_exc()
        _write_startup_failure("start", failure)
        _show_failure("failed to load", failure)


def stop(context) -> None:
    """Unload the UI and release log files."""

    try:
        if _command_module:
            _command_module.stop()
    except Exception:
        failure = traceback.format_exc()
        _write_startup_failure("stop", failure)
        _show_failure("did not unload cleanly", failure)
    finally:
        try:
            logging_module = importlib.import_module(
                f"{RUNTIME_PACKAGE}.utilities.logging_utils"
            )
            logging_module.close_handlers()
        except Exception:
            pass


def _load_command_module() -> Any:
    """Load project modules under an add-in-specific runtime namespace."""

    addin_folder = os.path.dirname(os.path.abspath(__file__))
    package = sys.modules.get(RUNTIME_PACKAGE)
    if package is None:
        package = types.ModuleType(RUNTIME_PACKAGE)
        package.__path__ = [addin_folder]
        package.__package__ = RUNTIME_PACKAGE
        sys.modules[RUNTIME_PACKAGE] = package
    return importlib.import_module(
        f"{RUNTIME_PACKAGE}.commands.export_cutting_dxf"
    )


def _write_startup_failure(stage: str, failure: str) -> None:
    """Persist import-time failures even when the main logger cannot load."""

    try:
        with STARTUP_LOG.open("a", encoding="utf-8") as stream:
            timestamp = datetime.now(timezone.utc).isoformat()
            stream.write(f"\n{timestamp} | {stage}\n{failure}\n")
    except Exception:
        pass


def _show_failure(action: str, failure: str) -> None:
    """Show the complete startup failure and fallback log location."""

    application = adsk.core.Application.get()
    if application:
        application.userInterface.messageBox(
            f"Cutting DXF Exporter {action}:\n\n{failure}\n"
            f"Startup log: {STARTUP_LOG}",
            "Cutting DXF Exporter",
        )
