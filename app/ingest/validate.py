"""Upload validation: extension allowlist, size cap, and duplicate-content
rejection.

`spec: document-ingestion` § "Supported upload formats" and § "Upload
validation" require every rejection to be actionable: the accepted formats
are named on an extension rejection, the configured limit is named on an
oversize rejection, and the conflicting document is named on a duplicate.
Validation never touches the filesystem or creates a document row — it
only inspects the bytes already read into memory and looks up `documents`
by content hash, so a caller can validate before deciding to persist
anything.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, select

from app.config import AppConfig
from app.db import documents as documents_table


class ValidationError(Exception):
    """Raised when an upload fails validation.

    The message is written to be shown to the admin verbatim — every raise
    site names the specific limit or conflicting document, never a bare
    status code.
    """


@dataclass(frozen=True)
class ValidatedUpload:
    """The result of a successful validation: everything the caller needs
    to insert the `documents` row.
    """

    filename: str
    ext: str
    size_bytes: int
    sha256: str


def validate_upload(
    filename: str,
    content: bytes,
    cfg: AppConfig,
    engine: Engine,
) -> ValidatedUpload:
    """Validate `content` (the full bytes of an uploaded file named
    `filename`) against `cfg.ingestion`'s extension allowlist and size cap,
    and against `documents.sha256` for a content duplicate.

    Raises `ValidationError` naming the specific problem on any failure.
    """
    ext = Path(filename).suffix.lower()
    allowed = cfg.ingestion.allowed_extensions
    if ext not in allowed:
        raise ValidationError(
            f"{ext or filename!r} is not a supported format; "
            f"accepted formats: {', '.join(allowed)}"
        )

    max_bytes = cfg.ingestion.max_upload_mb * 1024 * 1024
    size_bytes = len(content)
    if size_bytes > max_bytes:
        raise ValidationError(
            f"upload is {size_bytes} bytes, exceeding the configured "
            f"limit of {cfg.ingestion.max_upload_mb} MB"
        )

    sha256 = hashlib.sha256(content).hexdigest()
    with engine.connect() as conn:
        existing = conn.execute(
            select(documents_table.c.filename).where(
                documents_table.c.sha256 == sha256
            )
        ).first()
    if existing is not None:
        raise ValidationError(
            f"duplicate content: this file's content already exists as "
            f"the document {existing.filename!r}"
        )

    return ValidatedUpload(
        filename=filename, ext=ext, size_bytes=size_bytes, sha256=sha256
    )
