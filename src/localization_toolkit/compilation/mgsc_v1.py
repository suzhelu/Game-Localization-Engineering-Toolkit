"""Finite, read-only validation of the audited MGSC v1 envelope.

The payload is deliberately opaque. This module validates only the header,
tables, bounds, main-block presence, and exact declared payload length that the
source project's validator checked; it is not an MG decoder or compiler.
"""
from __future__ import annotations

import struct
from pathlib import Path

from ..hashing import hash_file


class MGSCValidationError(ValueError):
    pass


def _int32(data: bytes, position: int, label: str) -> tuple[int, int]:
    if position + 4 > len(data):
        raise MGSCValidationError(f"Truncated MGSC field: {label}")
    return struct.unpack_from("<i", data, position)[0], position + 4


def _dotnet_7bit_length(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 35, 7):
        if position >= len(data):
            raise MGSCValidationError("Truncated .NET string length")
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if value > 0x7FFFFFFF:
                raise MGSCValidationError(".NET string length exceeds Int32")
            return value, position
    raise MGSCValidationError("Invalid .NET 7-bit encoded string length")


class MGSCV1Validator:
    """Validate only the explicit MGSC v1 envelope rules from the source audit."""

    validator_id = "mgsc-v1-envelope-readonly-v1"

    def validate(self, path: Path) -> dict[str, object]:
        path = Path(path)
        data = path.read_bytes()
        if len(data) < 16 or data[:4] != b"MGSC":
            raise MGSCValidationError("Missing MGSC header or fixed header is truncated")
        position = 4
        version, position = _int32(data, position, "version")
        if version != 1:
            raise MGSCValidationError(f"Unsupported MGSC version: {version}")
        block_count, position = _int32(data, position, "block count")
        line_count, position = _int32(data, position, "line count")
        data_bytes, position = _int32(data, position, "data byte count")
        if not 1 <= block_count <= 10_000:
            raise MGSCValidationError("Invalid block count")
        if not 1 <= line_count <= 1_000_000:
            raise MGSCValidationError("Invalid line count")
        if data_bytes < 1:
            raise MGSCValidationError("Invalid declared data length")

        block_table: list[dict[str, object]] = []
        for index in range(block_count):
            name_length, position = _dotnet_7bit_length(data, position)
            if position + name_length > len(data):
                raise MGSCValidationError(f"Invalid block name length at block {index}")
            raw_name = data[position:position + name_length]
            # The source project used Buffer.toString('utf8'), which replaces
            # malformed sequences rather than rejecting the entire artifact.
            name = raw_name.decode("utf-8", errors="replace")
            position += name_length
            offset, position = _int32(data, position, f"block {index} offset")
            if not 0 <= offset < data_bytes:
                raise MGSCValidationError(f"Block offset outside declared payload at block {index}")
            block_table.append({"name": name, "offset": offset})
        if not any(item["name"] == "main" for item in block_table):
            raise MGSCValidationError("MGSC block table has no main block")

        line_offsets: list[int] = []
        for index in range(line_count):
            offset, position = _int32(data, position, f"line table offset {index}")
            if not 0 <= offset < data_bytes:
                raise MGSCValidationError(f"Line offset outside declared payload at row {index}")
            line_offsets.append(offset)
        if len(data) - position != data_bytes:
            raise MGSCValidationError("Declared payload length does not match remaining file bytes")

        return {
            "validator_id": self.validator_id,
            "format": "MGSC",
            "version": version,
            "block_count": block_count,
            "line_count": line_count,
            "data_bytes": data_bytes,
            "file_bytes": len(data),
            "block_table": block_table,
            "line_offsets": line_offsets,
            "sha256": hash_file(path),
            "evidence_scope": "header, table and declared-length bounds only; payload remains opaque",
        }
