"""Stack-neutral adapters. Execute only against staging or dry-run targets."""

from __future__ import annotations

import importlib
import base64
import json
import os
import shlex
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .models import Scenario, TargetResult
from .transport import StreamPlan


class Target(ABC):
    @abstractmethod
    def execute(self, audio_path: Path, scenario: Scenario) -> TargetResult:
        """Run the developer's target once and return its observed output."""

    @property
    def can_intervene(self) -> bool:
        return False

    def intervene(self, audio_path: Path, scenario: Scenario, stage: str, replacement: Any) -> TargetResult:
        raise NotImplementedError("This target does not support stage intervention")

    @property
    def can_stream(self) -> bool:
        return False

    def execute_stream(self, stream: StreamPlan, scenario: Scenario) -> TargetResult:
        raise NotImplementedError("This target does not support streaming transport plans")


def _load_callable(reference: str):
    module, separator, name = reference.partition(":")
    if not separator:
        raise ValueError("Python callable must be module:function")
    # Scenario-local modules can be imported without installing the target.
    value: Any = importlib.import_module(module)
    for part in name.split("."):
        value = getattr(value, part)
    if not callable(value):
        raise TypeError(f"{reference} is not callable")
    return value


class PythonTarget(Target):
    def __init__(self, reference: str, scenario_dir: Path, intervention_reference: str | None = None,
                 stream_reference: str | None = None):
        self.reference = reference
        self.scenario_dir = scenario_dir
        self.intervention_reference = intervention_reference
        self.stream_reference = stream_reference

    @property
    def can_intervene(self) -> bool:
        return self.intervention_reference is not None

    @property
    def can_stream(self) -> bool:
        return self.stream_reference is not None

    def execute(self, audio_path: Path, scenario: Scenario) -> TargetResult:
        added = str(self.scenario_dir)
        sys.path.insert(0, added)
        try:
            return TargetResult.from_value(_load_callable(self.reference)(audio_path, scenario))
        finally:
            sys.path.remove(added)

    def intervene(self, audio_path: Path, scenario: Scenario, stage: str, replacement: Any) -> TargetResult:
        if not self.intervention_reference:
            return super().intervene(audio_path, scenario, stage, replacement)
        added = str(self.scenario_dir)
        sys.path.insert(0, added)
        try:
            function = _load_callable(self.intervention_reference)
            return TargetResult.from_value(function(audio_path, scenario, stage, replacement))
        finally:
            sys.path.remove(added)

    def execute_stream(self, stream: StreamPlan, scenario: Scenario) -> TargetResult:
        if not self.stream_reference:
            return super().execute_stream(stream, scenario)
        added = str(self.scenario_dir)
        sys.path.insert(0, added)
        try:
            return TargetResult.from_value(_load_callable(self.stream_reference)(stream, scenario))
        finally:
            sys.path.remove(added)


class CommandTarget(Target):
    def __init__(self, command: str | list[str], cwd: Path, timeout: float = 60,
                 intervention_command: str | list[str] | None = None,
                 stream_command: str | list[str] | None = None,
                 environment: dict[str, str] | None = None,
                 environment_from: dict[str, str] | None = None):
        self.command, self.cwd, self.timeout = command, cwd, timeout
        self.intervention_command = intervention_command
        self.stream_command = stream_command
        self.environment = environment or {}
        self.environment_from = environment_from or {}

    @property
    def can_intervene(self) -> bool:
        return self.intervention_command is not None

    @property
    def can_stream(self) -> bool:
        return self.stream_command is not None

    def _args(self, command: str | list[str], audio_path: Path) -> list[str]:
        if isinstance(command, str):
            args = shlex.split(command, posix=os.name != "nt")
            if os.name == "nt":
                args = [arg[1:-1] if len(arg) >= 2 and arg[0] == arg[-1] == '"' else arg for arg in args]
        else:
            args = list(command)
        return [str(arg).replace("{audio}", str(audio_path)) for arg in args]

    def _environment(self) -> dict[str, str]:
        result = os.environ.copy()
        result.update({str(key): str(value) for key, value in self.environment.items()})
        for child_name, host_name in self.environment_from.items():
            if host_name not in os.environ:
                raise ValueError(f"Required environment variable is missing: {host_name}")
            result[str(child_name)] = os.environ[host_name]
        return result

    def execute(self, audio_path: Path, scenario: Scenario) -> TargetResult:
        try:
            completed = subprocess.run(self._args(self.command, audio_path), cwd=self.cwd,
                                       env=self._environment(), text=True, capture_output=True,
                                       timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Target command timed out after {self.timeout:g} seconds") from exc
        if completed.returncode != 0:
            raise RuntimeError(f"Target exited {completed.returncode}: {completed.stderr[-1000:]}")
        try:
            return TargetResult.from_value(json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            raise ValueError("Command target must print one JSON value to stdout") from exc

    def intervene(self, audio_path: Path, scenario: Scenario, stage: str, replacement: Any) -> TargetResult:
        if self.intervention_command is None:
            return super().intervene(audio_path, scenario, stage, replacement)
        payload = json.dumps({"stage": stage, "replacement": replacement}, ensure_ascii=False)
        try:
            completed = subprocess.run(self._args(self.intervention_command, audio_path), cwd=self.cwd,
                                       env=self._environment(), text=True,
                                       input=payload, capture_output=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Intervention command timed out after {self.timeout:g} seconds") from exc
        if completed.returncode != 0:
            raise RuntimeError(f"Intervention target exited {completed.returncode}: {completed.stderr[-1000:]}")
        try:
            return TargetResult.from_value(json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            raise ValueError("Intervention command must print one JSON value to stdout") from exc

    def execute_stream(self, stream: StreamPlan, scenario: Scenario) -> TargetResult:
        if self.stream_command is None:
            return super().execute_stream(stream, scenario)
        try:
            completed = subprocess.run(self._args(self.stream_command, Path("stream-plan")), cwd=self.cwd,
                                       env=self._environment(), text=True,
                                       input=json.dumps(stream.to_dict(), ensure_ascii=False), capture_output=True,
                                       timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Streaming command timed out after {self.timeout:g} seconds") from exc
        if completed.returncode != 0:
            raise RuntimeError(f"Streaming target exited {completed.returncode}: {completed.stderr[-1000:]}")
        try:
            return TargetResult.from_value(json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            raise ValueError("Streaming command must print one JSON value to stdout") from exc


class HTTPTarget(Target):
    def __init__(self, url: str, timeout: float = 60, headers: dict[str, str] | None = None,
                 intervention_url: str | None = None, stream_url: str | None = None,
                 headers_env: dict[str, str] | None = None):
        self.url, self.timeout, self.headers = url, timeout, headers or {}
        self.intervention_url = intervention_url
        self.stream_url = stream_url
        self.headers_env = headers_env or {}

    def _headers(self) -> dict[str, str]:
        result = dict(self.headers)
        for header, variable in self.headers_env.items():
            if variable not in os.environ:
                raise ValueError(f"Required environment variable is missing: {variable}")
            result[str(header)] = os.environ[variable]
        return result

    @property
    def can_intervene(self) -> bool:
        return self.intervention_url is not None

    @property
    def can_stream(self) -> bool:
        return self.stream_url is not None

    def execute(self, audio_path: Path, scenario: Scenario) -> TargetResult:
        request = Request(self.url, data=audio_path.read_bytes(), method="POST",
                          headers={"Content-Type": "audio/wav", "X-VoiceShrink-Scenario": scenario.name, **self._headers()})
        with urlopen(request, timeout=self.timeout) as response:
            return TargetResult.from_value(json.loads(response.read().decode("utf-8")))

    def intervene(self, audio_path: Path, scenario: Scenario, stage: str, replacement: Any) -> TargetResult:
        if not self.intervention_url:
            return super().intervene(audio_path, scenario, stage, replacement)
        body = json.dumps({"audio_base64": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
                           "stage": stage, "replacement": replacement}, ensure_ascii=False).encode("utf-8")
        request = Request(self.intervention_url, data=body, method="POST",
                          headers={"Content-Type": "application/json", "X-VoiceShrink-Scenario": scenario.name,
                                   **self._headers()})
        with urlopen(request, timeout=self.timeout) as response:
            return TargetResult.from_value(json.loads(response.read().decode("utf-8")))

    def execute_stream(self, stream: StreamPlan, scenario: Scenario) -> TargetResult:
        if not self.stream_url:
            return super().execute_stream(stream, scenario)
        body = json.dumps(stream.to_dict(), ensure_ascii=False).encode("utf-8")
        request = Request(self.stream_url, data=body, method="POST",
                          headers={"Content-Type": "application/json", "X-VoiceShrink-Scenario": scenario.name,
                                   **self._headers()})
        with urlopen(request, timeout=self.timeout) as response:
            return TargetResult.from_value(json.loads(response.read().decode("utf-8")))


def create_target(scenario: Scenario) -> Target:
    config = scenario.target
    kind = config.get("type")
    workdir = Path(config.get("working_dir") or (scenario.path.parent if scenario.path else Path.cwd()))
    if kind == "python":
        return PythonTarget(config["callable"], workdir, config.get("intervention_callable"),
                            config.get("stream_callable"))
    if kind == "command":
        return CommandTarget(config["command"], workdir,
                             float(config.get("timeout", 60)), config.get("intervention_command"),
                             config.get("stream_command"), config.get("environment"), config.get("environment_from"))
    if kind == "http":
        return HTTPTarget(config["url"], float(config.get("timeout", 60)), config.get("headers"),
                          config.get("intervention_url"), config.get("stream_url"), config.get("headers_env"))
    if kind == "faster-whisper":
        from .integrations import FasterWhisperTarget
        return FasterWhisperTarget(config)
    raise ValueError(f"Unknown target type: {kind!r}")
