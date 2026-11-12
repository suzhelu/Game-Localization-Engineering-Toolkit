"""Streaming SHA-256 helpers."""
from __future__ import annotations
import os
import stat
import hashlib
from pathlib import Path

def hash_file(path: os.PathLike[str] | str, chunk_size: int = 1024 * 1024) -> str:
    """Return an uppercase SHA-256 hex digest for a regular, non-link file."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    file_path = Path(path)
    info = file_path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & reparse_flag):
        raise ValueError(f"Refusing to hash a symbolic link or reparse point: {file_path}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Expected a regular file: {file_path}")
    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        for block in iter(lambda: stream.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest().upper()
