"""Helper functions for the RFID Jukebox integration."""
import asyncio
import logging
from typing import Dict

import yaml
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def load_mappings(file_path: str) -> Dict[str, Dict[str, str]]:
    """Load tag mappings from a YAML file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            mappings = yaml.safe_load(f)
            if isinstance(mappings, dict):
                _LOGGER.info("Loaded %d mappings from %s", len(mappings), file_path)
                return mappings
            _LOGGER.warning("Mapping file %s is not a valid dictionary.", file_path)
            return {}
    except FileNotFoundError:
        _LOGGER.debug("Mapping file not found at %s, starting with empty map.", file_path)
        return {}
    except yaml.YAMLError as e:
        _LOGGER.error("Error reading mapping file %s: %s", file_path, e)
        return {}


def save_mappings(file_path: str, mappings: Dict[str, Dict[str, str]]) -> None:
    """Save tag mappings to a YAML file."""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(mappings, f, default_flow_style=False)
            _LOGGER.info("Saved %d mappings to %s", len(mappings), file_path)
    except OSError as e:
        _LOGGER.error("Error saving mapping file %s: %s", file_path, e)


class MappingStore:
    """A single, file-backed set of tag mappings shared by every jukebox config entry.

    Multiple RFID Jukebox instances (different tag sensor / media player pairs) can
    point at the same mapping file. Loading/saving is serialized through one lock so
    two instances mapping tags around the same time can't clobber each other's writes.
    """

    def __init__(self, hass: HomeAssistant, file_path: str) -> None:
        self._hass = hass
        self._file_path = file_path
        self._lock = asyncio.Lock()
        self._mappings: Dict[str, Dict[str, str]] = {}
        self._loaded = False

    @property
    def mappings(self) -> Dict[str, Dict[str, str]]:
        """Return the current in-memory mappings."""
        return self._mappings

    async def async_ensure_loaded(self) -> None:
        """Load the mapping file from disk, once, no matter how many entries share it."""
        async with self._lock:
            if self._loaded:
                return
            self._mappings = await self._hass.async_add_executor_job(
                load_mappings, self._file_path
            )
            self._loaded = True

    async def async_set_mapping(self, tag_id: str, mapping: Dict[str, str]) -> None:
        """Add/update a mapping and persist the whole map."""
        async with self._lock:
            self._mappings[tag_id] = mapping
            await self._hass.async_add_executor_job(
                save_mappings, self._file_path, dict(self._mappings)
            )
