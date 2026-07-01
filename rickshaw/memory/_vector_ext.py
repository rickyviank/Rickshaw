"""Optional integration with the sqlite-vector extension.

sqlite-vector (https://github.com/sqliteai/sqlite-vector) is a loadable SQLite
extension providing indexed / SIMD-accelerated vector search. It is distributed
as a prebuilt binary via the ``sqliteai-vector`` PyPI package (importable as
``sqlite_vector``), NOT as a pure-Python library.

Extension loading requires a Python ``sqlite3`` built with
``enable_load_extension`` support. Many stock builds compile it out
(``SQLITE_OMIT_LOAD_EXTENSION``), in which case we transparently fall back to the
brute-force cosine scan implemented in :mod:`rickshaw.memory.store`.

Install with::

    pip install sqliteai-vector
"""

from __future__ import annotations

import logging
import struct

logger = logging.getLogger(__name__)


def pack_float32(vec: list[float]) -> bytes:
    """Serialize a float vector to a little-endian FLOAT32 blob."""
    return struct.pack(f"<{len(vec)}f", *vec)


def try_load_extension(conn) -> bool:
    """Best-effort load of the sqlite-vector extension into *conn*.

    Returns ``True`` if the extension loaded and is usable, ``False`` otherwise
    (missing package, sqlite3 without extension support, or any load error). A
    ``False`` return is expected and non-fatal — callers fall back to the
    brute-force cosine scan.
    """
    if not hasattr(conn, "enable_load_extension"):
        logger.warning(
            "sqlite3 build lacks enable_load_extension support; "
            "using brute-force cosine fallback for vector search."
        )
        return False

    try:
        import importlib.resources

        ext_path = importlib.resources.files("sqlite_vector.binaries") / "vector"
        conn.enable_load_extension(True)
        conn.load_extension(str(ext_path))
        conn.enable_load_extension(False)
        # Sanity check the extension actually registered its functions.
        conn.execute("SELECT vector_version()").fetchone()
        return True
    except Exception as exc:  # ImportError, sqlite3 errors, etc.
        logger.warning(
            "sqlite-vector extension unavailable (%s); "
            "using brute-force cosine fallback for vector search.",
            exc,
        )
        return False
