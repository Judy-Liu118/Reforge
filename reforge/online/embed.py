"""Local sentence embeddings for semantic card recall (pre-registration v3).

bge-m3 dense vectors: CLS pooling, L2-normalised, run locally (no API calls,
no conversation budget). Vectors are cached on disk by sha256(text) so a
resumed run and the offline calibration see identical numbers.

The loop takes any `embed(text) -> list[float]` callable; tests pass a fake.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

DEFAULT_MODEL_DIR = Path("D:/models/bge-m3")
MAX_LEN = 512


def cosine(a, b) -> float:
    """Inputs are normalised by the encoder; the norm guard keeps fakes honest."""
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


class Encoder:
    def __init__(self, model_dir: Path | str = DEFAULT_MODEL_DIR, cache_path: Path | str | None = None,
                 device: str | None = None) -> None:
        self.model_dir = Path(model_dir)
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, list[float]] = {}
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._cache[r["k"]] = r["v"]
        self._device = device
        self._model = self._tok = None

    @property
    def model_id(self) -> str:
        cfg = self.model_dir / "config.json"
        h = hashlib.sha256(cfg.read_bytes()).hexdigest()[:12] if cfg.exists() else "missing"
        return f"{self.model_dir.name}@{h}"

    def _load(self) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._tok = AutoTokenizer.from_pretrained(self.model_dir)
        self._model = AutoModel.from_pretrained(self.model_dir).to(self._device).eval()
        if self._device == "cuda":
            self._model = self._model.half()

    def encode(self, texts: list[str], batch: int = 16) -> list[list[float]]:
        keys = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
        todo = [(k, t) for k, t in dict(zip(keys, texts)).items() if k not in self._cache]
        if todo:
            import torch

            if self._model is None:
                self._load()
            new = []
            for i in range(0, len(todo), batch):
                chunk = todo[i:i + batch]
                enc = self._tok([t for _, t in chunk], padding=True, truncation=True, max_length=MAX_LEN,
                                return_tensors="pt").to(self._device)
                with torch.no_grad():
                    cls = self._model(**enc).last_hidden_state[:, 0].float()
                cls = torch.nn.functional.normalize(cls, dim=-1).cpu().tolist()
                for (k, _), v in zip(chunk, cls):
                    v = [round(x, 6) for x in v]
                    self._cache[k] = v
                    new.append({"k": k, "v": v})
            if self.cache_path:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.cache_path, "a", encoding="utf-8") as fh:
                    for r in new:
                        fh.write(json.dumps(r) + "\n")
        return [self._cache[k] for k in keys]

    def __call__(self, text: str) -> list[float]:
        return self.encode([text])[0]
