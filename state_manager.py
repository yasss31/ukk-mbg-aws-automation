"""
state_manager.py - Pengelola State Resource Tercipta
Menyimpan ID, ARN, dan endpoint resource yang telah dibangun untuk menjamin sifat idempotent.
"""
import json
from pathlib import Path
import config

class StateManager:
    def __init__(self, path=config.STATE_FILE):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        if self.path.is_file():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, mapping):
        self.data.update(mapping)
        self.save()

    def clear(self):
        self.data = {}
        self.save()

