"""Bot features. Each module declares its handlers with
telethon.events.register and then app.register_modules() wires them up.

A module with __MODULE__ and __HELP__ gets a page in the /help menu."""
import importlib
import pkgutil


def discover_modules() -> list:
    """Import every module in this package (found by path, so the
    current working directory does not matter)"""
    return [
        importlib.import_module(f"{__name__}.{info.name}")
        for info in pkgutil.iter_modules(__path__)
        if not info.name.startswith("_")
    ]
