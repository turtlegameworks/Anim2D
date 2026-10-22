"""Runs package generation on a worker thread and reports back through Qt signals."""

from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from ..models.preset import PresetDefinition
from ..models.settings import EffectSettings
from ..packaging.package import GenerationResult, write_package


class WorkerSignals(QObject):
    finished = Signal(object)  # GenerationResult
    failed = Signal(str)


class GenerateJob(QRunnable):
    def __init__(self, preset: PresetDefinition, settings: EffectSettings, destination: Path):
        super().__init__()
        self.preset = preset
        self.settings = settings
        self.destination = Path(destination)
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result: GenerationResult = write_package(self.preset, self.settings, self.destination)
        except Exception as exc:  # report, never crash the UI
            traceback.print_exc()
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.finished.emit(result)
