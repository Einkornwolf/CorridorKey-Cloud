"""Path security utilities (CRKY-56, CRKY-57).

Prevents path traversal, zip slip, and other filesystem attacks.
Use safe_join() anywhere a user-supplied filename is combined with
a trusted base directory.
"""

from __future__ import annotations

import os
import zipfile

from fastapi import HTTPException


def safe_join(base: str, *parts: str) -> str:
    """Join path components and verify the result stays within base.

    Raises HTTP 400 if the resolved path escapes the base directory.
    Handles .., encoded traversals, null bytes, and symlinks.
    """
    # Reject null bytes (can truncate paths in C-backed libs)
    for part in parts:
        if "\x00" in part:
            raise HTTPException(status_code=400, detail="Invalid filename: null byte")

    joined = os.path.join(base, *parts)
    resolved = os.path.realpath(joined)
    base_resolved = os.path.realpath(base)

    # Ensure resolved path is within base (with trailing sep to prevent prefix attacks)
    if not (resolved == base_resolved or resolved.startswith(base_resolved + os.sep)):
        raise HTTPException(status_code=400, detail="Invalid path: directory traversal detected")

    return resolved


_MAX_ZIP_MEMBERS = 50_000  # sane limit for VFX sequences
_MAX_EXTRACTED_BYTES = 20 * 1024**3  # 20 GB decompressed limit

# Only extract files with these extensions from zips — reject everything else
_ALLOWED_ZIP_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".bmp", ".dpx",  # images
    ".mp4", ".mov", ".avi", ".mkv", ".mxf", ".webm",  # video (rare in zips but valid)
})


def safe_extract_zip(zf: zipfile.ZipFile, target_dir: str) -> list[str]:
    """Extract a zip file safely, preventing zip slip.

    Validates each member's resolved path stays within target_dir.
    Enforces limits on member count and total decompressed size.
    Skips files with disallowed extensions.
    Returns the list of extracted file paths.
    """
    target_resolved = os.path.realpath(target_dir)
    extracted = []
    total_bytes = 0
    file_count = 0

    for member in zf.infolist():
        # Skip directories
        if member.is_dir():
            member_dir = os.path.join(target_dir, member.filename)
            resolved = os.path.realpath(member_dir)
            if not (resolved == target_resolved or resolved.startswith(target_resolved + os.sep)):
                raise HTTPException(status_code=400, detail="Zip slip detected")
            os.makedirs(resolved, exist_ok=True)
            continue

        file_count += 1
        if file_count > _MAX_ZIP_MEMBERS:
            raise HTTPException(status_code=400, detail=f"Zip contains too many files (max {_MAX_ZIP_MEMBERS})")

        # Skip files with disallowed extensions
        ext = os.path.splitext(member.filename)[1].lower()
        if ext not in _ALLOWED_ZIP_EXTS:
            continue

        # Validate file path
        member_path = os.path.join(target_dir, member.filename)
        resolved = os.path.realpath(member_path)
        if not resolved.startswith(target_resolved + os.sep):
            raise HTTPException(status_code=400, detail="Zip slip detected")

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(resolved), exist_ok=True)

        # Extract single member with decompressed size tracking
        with zf.open(member) as src, open(resolved, "wb") as dst:
            while True:
                chunk = src.read(8 * 1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > _MAX_EXTRACTED_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Zip decompressed size exceeds limit ({_MAX_EXTRACTED_BYTES // (1024**3)} GB)",
                    )
                dst.write(chunk)

        extracted.append(resolved)

    return extracted
