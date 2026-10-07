"""Maps a platform name onto the handler that knows how to apply there."""
from __future__ import annotations

from automation.platform_handlers.base_handler import BaseApplicationHandler
from automation.platform_handlers.generic_handler import GenericHandler
from automation.platform_handlers.linkedin_handler import LinkedInHandler

_HANDLERS: dict[str, type[BaseApplicationHandler]] = {
    LinkedInHandler.platform_name: LinkedInHandler,
    GenericHandler.platform_name: GenericHandler,
}


def get_handler(platform: str) -> BaseApplicationHandler:
    """Return a handler instance, falling back to the generic form filler."""
    handler_cls = _HANDLERS.get((platform or "").lower(), GenericHandler)
    return handler_cls()


def register_handler(handler_cls: type[BaseApplicationHandler]) -> None:
    _HANDLERS[handler_cls.platform_name.lower()] = handler_cls


def available_handlers() -> list[str]:
    return sorted(_HANDLERS)
