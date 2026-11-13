"""Explicit, fail-closed text-adapter registry and read-only format probing."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from typing import Any, Callable, Mapping, Protocol

from ..models import ExtractionResult, TextSlot


class AdapterRegistryError(ValueError):
    """An adapter registration, capability or selection error."""


class ProbeStatus(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    NEEDS_CONFIRMATION = "needs_confirmation"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class FormatProbe:
    status: ProbeStatus
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class FormatValidation:
    valid: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    version: str
    formats: tuple[str, ...]
    capabilities: frozenset[str]
    description: str = ""


class RegisteredTextAdapter(Protocol):
    adapter_id: str
    version: str
    formats: tuple[str, ...]
    capabilities: frozenset[str]

    def probe(self, source: str) -> FormatProbe: ...
    def extract(self, source: str, script: str, reference_source: str | None = None) -> ExtractionResult: ...
    def write(self, parsed: ExtractionResult, translations: Mapping[str, Any]) -> str: ...


class FormatValidatingAdapter(RegisteredTextAdapter, Protocol):
    def validate_format(self, source: str) -> FormatValidation: ...


AdapterFactory = Callable[[Mapping[str, Any]], RegisteredTextAdapter]

_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")
_CAPABILITIES = frozenset({"extract", "writeback", "format_probe", "format_validate"})


class AdapterRegistry:
    """Registry with optional, explicitly selected Python entry-point plugins.

    Plugin entry points use group ``localization_toolkit.adapters``. Their
    loaded callable receives this registry and must register its adapter. Code
    for an installed selected plugin executes in-process; users must trust and
    review that package before selecting it.
    """

    ENTRY_POINT_GROUP = "localization_toolkit.adapters"

    def __init__(self, *, discover_plugins: bool = True) -> None:
        self._factories: dict[str, tuple[AdapterDescriptor, AdapterFactory]] = {}
        self._entry_points: dict[str, Any] = {}
        self._loaded_plugins: set[str] = set()
        if discover_plugins:
            self._discover_entry_points()

    @staticmethod
    def _validate_descriptor(descriptor: AdapterDescriptor) -> None:
        adapter_id = descriptor.adapter_id
        AdapterRegistry._validate_id(adapter_id)
        if not isinstance(descriptor.version, str) or not descriptor.version.strip():
            raise AdapterRegistryError("Adapter version is required")
        if (not isinstance(descriptor.formats, tuple) or not descriptor.formats
                or any(not isinstance(item, str) or not item.strip() for item in descriptor.formats)
                or len(set(descriptor.formats)) != len(descriptor.formats)):
            raise AdapterRegistryError("At least one explicit format/scope label is required")
        if not isinstance(descriptor.capabilities, frozenset) or not descriptor.capabilities <= _CAPABILITIES:
            raise AdapterRegistryError("Adapter declares an unknown capability")
        if not isinstance(descriptor.description, str):
            raise AdapterRegistryError("Adapter description must be text")
        if not {"extract", "writeback", "format_probe"} <= descriptor.capabilities:
            raise AdapterRegistryError("Text adapters must declare extract, writeback and format_probe")

    @staticmethod
    def _validate_id(adapter_id: object) -> None:
        if (not isinstance(adapter_id, str) or not adapter_id
                or any(char not in _ID_CHARS for char in adapter_id)
                or adapter_id.startswith((".", "-"))):
            raise AdapterRegistryError("Adapter ID must be a lowercase portable token")

    def register(self, descriptor: AdapterDescriptor, factory: AdapterFactory) -> None:
        self._validate_descriptor(descriptor)
        key = descriptor.adapter_id
        if key in self._factories or key in self._entry_points:
            raise AdapterRegistryError(f"Duplicate adapter ID: {key}")
        if not callable(factory):
            raise AdapterRegistryError("Adapter factory must be callable")
        self._factories[key] = (descriptor, factory)

    def _discover_entry_points(self) -> None:
        try:
            entries = metadata.entry_points()
            selected = entries.select(group=self.ENTRY_POINT_GROUP) if hasattr(entries, "select") else entries.get(self.ENTRY_POINT_GROUP, ())
        except Exception as exc:
            raise AdapterRegistryError(f"Cannot inspect adapter entry points: {exc}") from exc
        for entry in selected:
            key = str(entry.name)
            self._validate_id(key)
            if key in self._entry_points or key in self._factories:
                raise AdapterRegistryError(f"Duplicate adapter entry-point ID: {key}")
            self._entry_points[key] = entry

    def _load_selected_plugin(self, adapter_id: str) -> None:
        if adapter_id in self._loaded_plugins:
            return
        entry = self._entry_points.get(adapter_id)
        if entry is None:
            return
        before = set(self._factories)
        del self._entry_points[adapter_id]
        try:
            register_plugin = entry.load()
            if not callable(register_plugin):
                raise AdapterRegistryError("Adapter entry point must be a callable registry hook")
            register_plugin(self)
        except Exception as exc:
            for key in set(self._factories) - before:
                self._factories.pop(key, None)
            self._entry_points[adapter_id] = entry
            raise AdapterRegistryError(f"Adapter plugin {adapter_id!r} failed to register: {exc}") from exc
        added = set(self._factories) - before
        if adapter_id not in added or len(added) != 1:
            for key in added:
                self._factories.pop(key, None)
            self._entry_points[adapter_id] = entry
            raise AdapterRegistryError("Plugin hook must register exactly its entry-point ID")
        self._loaded_plugins.add(adapter_id)

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._factories) | set(self._entry_points)))

    def descriptors(self) -> tuple[AdapterDescriptor, ...]:
        return tuple(self._factories[key][0] for key in sorted(self._factories))

    def load_all_plugins(self) -> None:
        """Load every discovered plugin; this executes every installed hook."""
        for adapter_id in tuple(sorted(self._entry_points)):
            self._load_selected_plugin(adapter_id)

    def create(self, adapter_id: str, options: Mapping[str, Any] | None = None) -> RegisteredTextAdapter:
        self._load_selected_plugin(adapter_id)
        registered = self._factories.get(adapter_id)
        if registered is None:
            raise AdapterRegistryError(
                f"Unknown text adapter {adapter_id!r}; available adapters: {', '.join(self.adapter_ids) or '(none)'}"
            )
        descriptor, factory = registered
        try:
            adapter = factory(dict(options or {}))
        except Exception as exc:
            raise AdapterRegistryError(f"Adapter {adapter_id!r} could not be configured: {exc}") from exc
        self._validate_instance(descriptor, adapter)
        return adapter

    @staticmethod
    def _validate_instance(descriptor: AdapterDescriptor, adapter: object) -> None:
        for key in ("adapter_id", "version", "formats", "capabilities"):
            if getattr(adapter, key, None) != getattr(descriptor, key):
                raise AdapterRegistryError(f"Adapter {descriptor.adapter_id!r} metadata mismatch for {key}")
        for name in ("extract", "write", "probe"):
            if not callable(getattr(adapter, name, None)):
                raise AdapterRegistryError(f"Adapter {descriptor.adapter_id!r} declares required capability but lacks {name}()")
        if "format_validate" in descriptor.capabilities and not callable(getattr(adapter, "validate_format", None)):
            raise AdapterRegistryError("Adapter claims format_validate but has no validate_format()")

    def probe(self, source: str, *, adapter_id: str | None = None,
              options: Mapping[str, Any] | None = None) -> dict[str, object]:
        """Probe decoded text without writing files or executing source content."""
        if not isinstance(source, str):
            raise TypeError("Probe input must be decoded text")
        ids = (adapter_id,) if adapter_id is not None else tuple(sorted(self._factories))
        results: list[dict[str, object]] = []
        for key in ids:
            adapter = self.create(key, options if key == adapter_id else None)
            try:
                result = adapter.probe(source)
            except Exception as exc:
                result = FormatProbe(ProbeStatus.NEEDS_CONFIRMATION,
                                     (f"Probe could not establish support: {type(exc).__name__}: {exc}",))
            if (not isinstance(result, FormatProbe) or not isinstance(result.status, ProbeStatus)
                    or not isinstance(result.evidence, tuple)
                    or not result.evidence or any(not isinstance(item, str) or not item for item in result.evidence)):
                raise AdapterRegistryError(f"Adapter {key!r} returned an invalid probe result")
            results.append({"adapter_id": key, "status": result.status.value,
                            "evidence": list(result.evidence)})
        supporters = [row for row in results if row["status"] == ProbeStatus.SUPPORTED.value]
        tentative = [row for row in results if row["status"] == ProbeStatus.NEEDS_CONFIRMATION.value]
        if len(supporters) > 1:
            status = ProbeStatus.AMBIGUOUS
        elif supporters:
            status = ProbeStatus.SUPPORTED
        elif tentative:
            status = ProbeStatus.NEEDS_CONFIRMATION
        else:
            status = ProbeStatus.UNSUPPORTED
        return {"schema_version": 1, "status": status.value, "results": results}

    def validate_format(self, adapter_id: str, source: str,
                        options: Mapping[str, Any] | None = None) -> FormatValidation:
        adapter = self.create(adapter_id, options)
        if "format_validate" not in adapter.capabilities:
            raise AdapterRegistryError(f"Adapter {adapter_id!r} does not declare format_validate")
        result = adapter.validate_format(source)
        if (not isinstance(result, FormatValidation) or type(result.valid) is not bool
                or not isinstance(result.evidence, tuple) or not result.evidence
                or any(not isinstance(item, str) or not item for item in result.evidence)):
            raise AdapterRegistryError(f"Adapter {adapter_id!r} returned an invalid format validation result")
        return result


def default_registry() -> AdapterRegistry:
    """Build a fresh default registry, loading only an explicitly selected plugin."""
    from .bgi import BgiAdapter
    descriptor = AdapterDescriptor("bgi", BgiAdapter.version, BgiAdapter.formats,
                                   BgiAdapter.capabilities, "Conservative explicit-schema BGI source adapter")
    registry = AdapterRegistry()
    registry.register(descriptor, lambda options: BgiAdapter.from_options(options))
    return registry
