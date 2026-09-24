"""Scenario loading. JSON is supported directly; a small YAML mapping/list subset is bundled."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value in ("null", "Null", "~"):
        return None
    if value[0] in ('"', "'") and value[-1] == value[0]:
        return json.loads(value) if value[0] == '"' else value[1:-1].replace("''", "'")
    try:
        return json.loads(value)
    except (ValueError, json.JSONDecodeError):
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            return [_scalar(part) for part in inner.split(",")] if inner else []
        return value


def _yaml_subset(source: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in source.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.strip() in ("---", "..."):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ValueError("YAML indentation must use spaces")
        lines.append((len(raw) - len(raw.lstrip()), raw.strip()))

    def parse(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines):
            return {}, index
        is_list = lines[index][1].startswith("- ")
        result: Any = [] if is_list else {}
        while index < len(lines):
            level, content = lines[index]
            if level < indent:
                break
            if level != indent:
                raise ValueError(f"Unexpected YAML indentation near {content!r}")
            if is_list:
                if not content.startswith("- "):
                    raise ValueError("Mixed YAML mapping and list at one level")
                item = content[2:].strip()
                index += 1
                if not item:
                    if index < len(lines) and lines[index][0] > indent:
                        value, index = parse(index, lines[index][0])
                    else:
                        value = None
                elif re.match(r"^[\w-]+:\s", item):
                    key, raw = item.split(":", 1)
                    value = {key: _scalar(raw)}
                    if index < len(lines) and lines[index][0] > indent:
                        extra, index = parse(index, lines[index][0])
                        if not isinstance(extra, dict):
                            raise ValueError("YAML list item must contain a mapping")
                        value.update(extra)
                else:
                    value = _scalar(item)
                result.append(value)
            else:
                if content.startswith("- ") or ":" not in content:
                    raise ValueError(f"Expected YAML key: value near {content!r}")
                key, raw = content.split(":", 1)
                key = key.strip()
                index += 1
                if raw.strip():
                    result[key] = _scalar(raw)
                elif index < len(lines) and lines[index][0] > indent:
                    result[key], index = parse(index, lines[index][0])
                else:
                    result[key] = None
        return result, index

    if not lines:
        raise ValueError("Scenario is empty")
    value, end = parse(0, lines[0][0])
    if end != len(lines):
        raise ValueError("Could not parse complete YAML document")
    return value


def load_data(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json" or source.lstrip().startswith("{"):
        data = json.loads(source)
    else:
        try:
            import yaml  # type: ignore
        except ImportError:
            data = _yaml_subset(source)
        else:
            data = yaml.safe_load(source)
    if not isinstance(data, dict):
        raise ValueError("Scenario root must be a mapping")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
