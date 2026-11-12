"""Provider protocol and deterministic local mock; no network implementation."""
from __future__ import annotations

from typing import Callable, Protocol, Sequence

from .models import ProviderResult, TranslationRequest


class AIProvider(Protocol):
    name: str

    def translate(self, request: TranslationRequest) -> Sequence[ProviderResult]: ...


class MockProvider:
    """Local test provider whose response function receives a structured request."""

    name = "local-mock"

    def __init__(self, responder: Callable[[TranslationRequest], Sequence[ProviderResult] | BaseException]):
        self.responder = responder
        self.requests: list[TranslationRequest] = []

    def translate(self, request: TranslationRequest) -> Sequence[ProviderResult]:
        self.requests.append(request)
        response = self.responder(request)
        if isinstance(response, BaseException):
            raise response
        return response
