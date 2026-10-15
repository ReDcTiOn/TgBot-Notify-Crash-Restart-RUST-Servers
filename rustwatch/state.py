from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class StateStore:
    """JSON-файл с состоянием серверов, чтобы перезапуск бота не терял контекст."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.exception("Не удалось прочитать %s, начинаю с чистого состояния", self._path)

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put(self, key: str, snapshot: dict) -> None:
        self._data[key] = snapshot

    def flush(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            log.exception("Не удалось сохранить %s", self._path)
