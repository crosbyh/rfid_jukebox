"""Button platform for RFID Jukebox."""
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button platform from a config entry."""
    jukebox = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MapTagButton(jukebox)])


class MapTagButton(ButtonEntity):
    """Representation of a Button entity for mapping tags."""

    def __init__(self, jukebox):
        """Initialize the button entity."""
        self._jukebox = jukebox
        self._attr_name = "Map Scanned Tag to Media"
        self._attr_unique_id = f"{DOMAIN}_{jukebox.entry.entry_id}_map_tag_button"
        self._attr_icon = "mdi:tag-plus"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, jukebox.entry.entry_id)},
            name=jukebox.entry.title,
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug("Map tag button pressed")

        # Read straight from the entities this jukebox instance tracks, rather than
        # guessing entity_ids: those change if the user renames an entity, and would
        # collide across jukebox instances anyway.
        media_type = self._jukebox.media_type_entity.current_option
        media_name = self._jukebox.text_entity.native_value
        alias = self._jukebox.alias_entity.native_value
        tag_id = self._jukebox.last_tag

        await self._jukebox.async_map_tag(tag_id, media_type, media_name, alias)
