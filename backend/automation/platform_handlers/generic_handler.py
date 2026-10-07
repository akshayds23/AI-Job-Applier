"""Generic handler for boards that link straight to an application form."""
from __future__ import annotations

from automation.platform_handlers.base_handler import BaseApplicationHandler


class GenericHandler(BaseApplicationHandler):
    platform_name = "generic"
    display_name = "Generic Application Form"
    requires_login = False
