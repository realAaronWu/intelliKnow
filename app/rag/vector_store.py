"""FAISS-backed vector store: one exact-search index per intent space.

Each space gets its own `IndexFlatIP` — an exhaustive inner-product scan —
wrapped in `IndexIDMap2` so vectors can be keyed by `chunk.id` directly
rather than by FAISS's own dense position, which would otherwise shift on
every removal. At this corpus size brute force is sub-millisecond, so there
is no ANN index here to tune and no recall loss to accept.

Vectors arrive already unit-normalized from the embedding provider (see
`app.providers.base.normalize`), so inner product **is** cosine similarity.
This module never re-normalizes — doing so here would silently mask a
provider bug that skipped normalization instead of surfacing it.

One index file per space (`{slug}.index` under `faiss_dir`) keeps routing
equivalent to file selection: reassigning a document moves vectors between
two files, deleting a space deletes one file, and a space that has never
been written to needs no file at all.
"""

from __future__ import annotations

import os
import tempfile
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Callable, TypeVar

import faiss
import numpy as np

_FAISS_LOCK = RLock()
_Method = TypeVar("_Method", bound=Callable)


def _synchronized(method: _Method) -> _Method:
    @wraps(method)
    def locked(self, *args, **kwargs):
        with _FAISS_LOCK:
            return method(self, *args, **kwargs)

    return locked  # type: ignore[return-value]


class VectorStore:
    """Owns one FAISS index per intent space, keyed by `chunk.id`.

    Indexes are held in memory once touched (created, loaded, or written
    to) and are not implicitly flushed to disk — callers persist explicitly
    via `persist(slug)`, mirroring the design's "write after each document
    completes" policy rather than this class guessing when a write is due.
    """

    def __init__(self, faiss_dir: Path, dimension: int) -> None:
        self._faiss_dir = Path(faiss_dir)
        self._faiss_dir.mkdir(parents=True, exist_ok=True)
        self._dimension = dimension
        self._indexes: dict[str, faiss.IndexIDMap2] = {}

    def _index_path(self, slug: str) -> Path:
        return self._faiss_dir / f"{slug}.index"

    def _new_index(self) -> faiss.IndexIDMap2:
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self._dimension))

    @_synchronized
    def _get_or_create(self, slug: str) -> faiss.IndexIDMap2:
        """Return the in-memory index for `slug`, loading it from disk or
        creating it empty if this is the first touch this process has seen.

        This is what lets `search` on a space nobody has explicitly created
        or loaded still return empty rather than raising — the brief's
        "searching a space with no vectors returns empty, never an error"
        applies just as much to a space that was never created as to one
        that was created and left empty.
        """
        if slug in self._indexes:
            return self._indexes[slug]
        path = self._index_path(slug)
        if path.exists():
            index = faiss.read_index(str(path))
        else:
            index = self._new_index()
        self._indexes[slug] = index
        return index

    @_synchronized
    def create_space(self, slug: str) -> None:
        """Ensure an empty in-memory index exists for `slug`. Idempotent."""
        self._get_or_create(slug)

    @_synchronized
    def add(self, slug: str, ids: list[int], vectors: list[list[float]]) -> None:
        index = self._get_or_create(slug)
        vector_array = np.array(vectors, dtype="float32")
        id_array = np.array(ids, dtype="int64")
        index.add_with_ids(vector_array, id_array)

    @_synchronized
    def remove(self, slug: str, ids: list[int]) -> None:
        index = self._get_or_create(slug)
        index.remove_ids(np.array(ids, dtype="int64"))

    @_synchronized
    def move(self, from_slug: str, to_slug: str, ids: list[int]) -> None:
        """Transfer `ids` from `from_slug` to `to_slug` without re-embedding.

        Vectors are reconstructed from the source index (FAISS stores them
        verbatim; `IndexIDMap2.reconstruct` looks them up by the external
        `chunk.id`, not by internal position) and re-inserted under the same
        ids in the destination index.
        """
        from_index = self._get_or_create(from_slug)
        id_array = np.array(ids, dtype="int64")
        vectors = np.array(
            [from_index.reconstruct(int(chunk_id)) for chunk_id in ids],
            dtype="float32",
        )
        from_index.remove_ids(id_array)

        to_index = self._get_or_create(to_slug)
        to_index.add_with_ids(vectors, id_array)

    @_synchronized
    def search(self, slug: str, vector: list[float], top_n: int) -> list[tuple[int, float]]:
        index = self._get_or_create(slug)
        if index.ntotal == 0:
            return []
        query = np.array([vector], dtype="float32")
        k = min(top_n, index.ntotal)
        scores, ids = index.search(query, k)
        results: list[tuple[int, float]] = []
        for found_id, score in zip(ids[0], scores[0]):
            if found_id == -1:
                continue
            results.append((int(found_id), float(score)))
        return results

    @_synchronized
    def delete_space(self, slug: str) -> None:
        self._indexes.pop(slug, None)
        path = self._index_path(slug)
        if path.exists():
            path.unlink()

    @_synchronized
    def rebuild_all(self, entries: dict[str, tuple[list[int], list[list[float]]]]) -> None:
        """Replace every space's index with one built from `entries`, and
        delete the index of any space `entries` does not mention.

        Atomic across all spaces: each new index is written to a temp file
        in `faiss_dir` first, and nothing is swapped into place until every
        one of them has been written. A failure part-way therefore leaves
        every existing `.index` exactly as it was, rather than the mix of
        rebuilt and stale spaces that rebuilding in place produces — which,
        for a full re-index under a new embedding model, is the mixed-model
        state `index_meta.json` exists to prevent, and it would go
        unrecorded because the metadata write never runs.

        Spaces absent from `entries` have no chunks left, so their index
        file is removed rather than left behind as a stale artifact holding
        vectors for chunks that no longer exist.
        """
        self._faiss_dir.mkdir(parents=True, exist_ok=True)

        staged: dict[str, Path] = {}
        try:
            for slug, (ids, vectors) in entries.items():
                index = self._new_index()
                if ids:
                    index.add_with_ids(
                        np.array(vectors, dtype="float32"),
                        np.array(ids, dtype="int64"),
                    )
                handle, temp_name = tempfile.mkstemp(
                    dir=self._faiss_dir, prefix=f".{slug}.", suffix=".rebuild"
                )
                os.close(handle)
                staged[slug] = Path(temp_name)
                faiss.write_index(index, temp_name)
        except BaseException:
            for temp_path in staged.values():
                temp_path.unlink(missing_ok=True)
            raise

        for slug, temp_path in staged.items():
            os.replace(temp_path, self._index_path(slug))
            # Drop the cached index so the next touch reads the new file.
            self._indexes.pop(slug, None)

        for index_file in self._faiss_dir.glob("*.index"):
            slug = index_file.stem
            if slug not in entries:
                index_file.unlink()
                self._indexes.pop(slug, None)

    @_synchronized
    def persist(self, slug: str) -> None:
        index = self._get_or_create(slug)
        faiss.write_index(index, str(self._index_path(slug)))

    @_synchronized
    def load(self, slug: str) -> None:
        """Force `slug`'s index to be (re)read from disk into memory,
        replacing any in-memory state — used to pick up a fresh `VectorStore`
        instance's view of what a prior instance persisted, and by callers
        that want to discard unpersisted in-memory changes.
        """
        path = self._index_path(slug)
        self._indexes[slug] = faiss.read_index(str(path)) if path.exists() else self._new_index()
