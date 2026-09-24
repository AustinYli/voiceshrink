"""Optional reference integrations kept outside VoiceShrink's core runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import Scenario, StageResult, TargetResult
from .targets import Target


class FasterWhisperTarget(Target):
    """Local faster-whisper ASR adapter, loaded only when selected by a scenario."""

    _models: dict[tuple[str, str, str], Any] = {}

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def execute(self, audio_path: Path, scenario: Scenario) -> TargetResult:
        try:
            from faster_whisper import WhisperModel
        except ModuleNotFoundError as exc:
            if exc.name == "faster_whisper":
                raise RuntimeError("Install the ASR extra first: pip install 'voiceshrink[asr]'") from exc
            raise RuntimeError(f"A faster-whisper dependency could not be loaded: {exc}") from exc
        except ImportError as exc:
            raise RuntimeError(f"The faster-whisper runtime could not be loaded: {exc}") from exc
        model_name = str(self.config.get("model", "tiny.en"))
        device = str(self.config.get("device", "cpu"))
        compute_type = str(self.config.get("compute_type", "int8"))
        key = (model_name, device, compute_type)
        download_root = self.config.get("download_root")
        if download_root:
            download_root = Path(download_root)
            if not download_root.is_absolute() and scenario.path:
                download_root = (scenario.path.parent / download_root).resolve()
            download_root.mkdir(parents=True, exist_ok=True)
        if key not in self._models:
            self._models[key] = WhisperModel(model_name, device=device, compute_type=compute_type,
                                             download_root=str(download_root) if download_root else None)
        segments, info = self._models[key].transcribe(
            str(audio_path), language=self.config.get("language"),
            beam_size=int(self.config.get("beam_size", 1)),
            vad_filter=bool(self.config.get("vad_filter", False)),
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return TargetResult({"transcript": text}, [StageResult("asr", text)],
                            {"language": getattr(info, "language", None), "model": model_name})
