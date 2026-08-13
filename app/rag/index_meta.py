"""Index metadata and the embedding-immutability check.

Vectors from different embedding models are not comparable, and
cross-space score fusion (RRF over per-space dense results) depends on
every space sharing one model. Swapping `embedding.model` after documents
are indexed would silently degrade every answer with no error — the worst
failure mode available — so this module records which model built the
index and turns a mismatch into a loud, early failure instead.

`data/index_meta.json` sits alongside `data/faiss/` (a sibling of
`faiss_dir`, not inside it) because it describes the index as a whole,
not any one space's file — see `design.md` § Data model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import faiss

from app.config import AppConfig

_META_FILENAME = "index_meta.json"


@dataclass(frozen=True)
class IndexMeta:
    """The embedding model and dimension recorded at first ingest."""

    model: str
    dimension: int


def _meta_path(faiss_dir: Path) -> Path:
    return Path(faiss_dir).parent / _META_FILENAME


def read_meta(faiss_dir: Path) -> IndexMeta | None:
    """Return the recorded index metadata, or `None` if nothing has been
    recorded yet (a fresh install, before the first document is indexed).
    """
    path = _meta_path(faiss_dir)
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return IndexMeta(model=raw["model"], dimension=raw["dimension"])


def write_meta(faiss_dir: Path, model: str, dimension: int) -> None:
    """Record `model`/`dimension` as the embedding config the index was
    built with, replacing any existing record.

    Only a full re-index may call this on an index that already has a
    record — the whole point of the record is that it names the model the
    stored vectors were produced with. Ingest uses `record_meta_if_absent`.
    """
    path = _meta_path(faiss_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "dimension": dimension}))


def record_meta_if_absent(faiss_dir: Path, model: str, dimension: int) -> None:
    """Record `model`/`dimension` if nothing has been recorded yet.

    `spec: document-ingestion` § "Embedding model recorded at first
    ingest". Called on every successful ingest, and a no-op on all but the
    first: an existing record names the model the vectors already in the
    index were built with, and restamping it with whatever happens to be
    configured now would erase exactly the mismatch `assert_compatible`
    exists to detect.
    """
    if read_meta(faiss_dir) is None:
        write_meta(faiss_dir, model=model, dimension=dimension)


def _has_any_vectors(faiss_dir: Path) -> bool:
    """Whether any space's index under `faiss_dir` holds at least one
    vector. Recorded metadata with an otherwise-empty index (every space
    created but nothing embedded yet, or every document since removed)
    still permits a model change — there is nothing to be incompatible
    with.
    """
    faiss_dir = Path(faiss_dir)
    if not faiss_dir.exists():
        return False
    for index_file in faiss_dir.glob("*.index"):
        index = faiss.read_index(str(index_file))
        if index.ntotal > 0:
            return True
    return False


def assert_compatible(cfg: AppConfig, faiss_dir: Path) -> None:
    """Raise `ValueError` if `cfg.embedding.model` differs from the model
    recorded for `faiss_dir` and the index actually holds vectors.

    No recorded meta (fresh install) or a recorded-but-empty index (nothing
    to be incompatible with) permits any model.

    `app/bootstrap.py` calls this two ways, and needs both: directly, at
    startup, because editing `config.yaml` and restarting is how an
    operator actually changes a model; and adapted into a `ConfigService`
    guard — `lambda old, new: assert_compatible(new, faiss_dir)` — so a
    live `update()` or `reload()` is rejected too.
    """
    meta = read_meta(faiss_dir)
    if meta is None:
        return
    if not _has_any_vectors(faiss_dir):
        return
    if cfg.embedding.model != meta.model:
        raise ValueError(
            f"embedding.model is configured as {cfg.embedding.model!r} but "
            f"the existing index was built with {meta.model!r}; vectors "
            "from different models are not comparable — re-index before "
            "changing the model"
        )
