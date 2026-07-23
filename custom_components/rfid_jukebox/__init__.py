"""The RFID Jukebox integration."""
import logging

from homeassistant.core import HomeAssistant, State
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import event
from homeassistant.const import STATE_IDLE, STATE_OFF, STATE_PAUSED, STATE_PLAYING


from .const import (
    DOMAIN,
    CONF_TAG_SENSOR,
    CONF_MEDIA_PLAYER,
    CONF_MA_FILESYSTEM,
    DEFAULT_MAPPING_FILE_PATH,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RFID Jukebox from a config entry."""
    from .helpers import MappingStore

    hass.data.setdefault(DOMAIN, {})

    # All jukebox instances (one per tag sensor / media player pair) share a single
    # mapping file, so the store is keyed separately from the per-entry jukeboxes below.
    mapping_store = hass.data[DOMAIN].get("_mapping_store")
    if mapping_store is None:
        mapping_file = hass.config.path(DEFAULT_MAPPING_FILE_PATH)
        mapping_store = MappingStore(hass, mapping_file)
        hass.data[DOMAIN]["_mapping_store"] = mapping_store
    await mapping_store.async_ensure_loaded()

    jukebox = RFIDJukebox(hass, entry, mapping_store)
    hass.data[DOMAIN][entry.entry_id] = jukebox

    await jukebox.async_setup()

    await hass.config_entries.async_forward_entry_setups(entry, ["text", "button", "select"])

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, ["text", "button", "select"]
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        # Only tear down the shared "map_tag" service once every jukebox is gone.
        if not _all_jukeboxes(hass):
            hass.services.async_remove(DOMAIN, "map_tag")

    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


def _all_jukeboxes(hass: HomeAssistant):
    """Return the RFIDJukebox instances currently registered for this integration."""
    return [v for k, v in hass.data.get(DOMAIN, {}).items() if k != "_mapping_store"]


async def _async_map_tag(
    hass: HomeAssistant, tag_id: str, media_type: str, media_name: str, alias: str = None
) -> None:
    """Create/update a tag mapping in the shared store.

    Mappings are shared by every jukebox instance (one file, possibly several tag
    sensor / media player pairs), so this isn't tied to any single config entry.
    """
    if not tag_id or not media_name:
        _LOGGER.error(
            "Cannot map tag. Tag ID or Media Name is missing. Tag: '%s', Media: '%s'",
            tag_id,
            media_name,
        )
        return

    _LOGGER.info("Mapping tag '%s' to %s '%s'", tag_id, media_type, media_name)
    mapping_store = hass.data[DOMAIN]["_mapping_store"]
    await mapping_store.async_set_mapping(
        tag_id, {"type": media_type, "name": media_name, "alias": alias or tag_id}
    )

    # If any jukebox currently has this tag "active", clear it so the next scan
    # re-evaluates the (now updated) mapping instead of replaying the old one.
    for jukebox in _all_jukeboxes(hass):
        if jukebox.current_tag == tag_id:
            jukebox.current_tag = None


class RFIDJukebox:
    """The main class for the RFID Jukebox integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, mapping_store):
        """Initialize the RFID Jukebox."""
        self.hass = hass
        self.entry = entry
        # entry.options is what the options flow writes to; entry.data holds the
        # original setup values. Merge them so options-flow edits actually apply.
        self.config = {**entry.data, **entry.options}
        self.mapping_store = mapping_store
        self.last_tag = None
        self.current_tag = None
        self.text_entity = None
        self.alias_entity = None
        self.media_type_entity = None
        self.last_played_playlist_name = None

    @property
    def mappings(self):
        """Return the shared tag mappings."""
        return self.mapping_store.mappings

    async def async_setup(self):
        """Set up the jukebox."""
        # Register state listener
        self.entry.async_on_unload(
            event.async_track_state_change_event(
                self.hass, self.config[CONF_TAG_SENSOR], self.async_tag_changed_handler
            )
        )

        # Register the shared map_tag service once, no matter how many jukeboxes exist.
        if not self.hass.services.has_service(DOMAIN, "map_tag"):
            hass = self.hass

            async def _handle_map_tag(call):
                await _async_map_tag(
                    hass,
                    call.data.get("tag_id"),
                    call.data.get("media_type", "playlist"),
                    call.data.get("media_name"),
                    call.data.get("alias"),
                )

            self.hass.services.async_register(DOMAIN, "map_tag", _handle_map_tag)

    async def async_tag_changed_handler(self, event_data):
        """Handle state changes for the RFID tag sensor."""
        new_state = event_data.data.get("new_state")
        if not new_state:
            return

        new_tag = new_state.state
        _LOGGER.debug("Tag sensor changed to: %s", new_tag)

        # Tag is presented
        if new_tag and new_tag.lower() not in ["none", "unknown", ""]:
            # Update the 'last_tag' sensor for the UI
            if self.last_tag != new_tag:
                self.last_tag = new_tag
                if new_tag in self.mappings:
                    mapping = self.mappings[new_tag]
                    if isinstance(mapping, dict):
                        alias = mapping.get("alias", new_tag)
                        media_type = mapping.get("type", "playlist")
                        media_name = mapping.get("name", "")
                        if self.text_entity:
                            self.text_entity.update_value(media_name)
                        if self.alias_entity:
                            self.alias_entity.update_value(alias)
                        if self.media_type_entity:
                            self.media_type_entity.update_option(media_type)
                    else:  # Handle old, simple mapping format
                        if self.text_entity:
                            self.text_entity.update_value(mapping)
                        if self.alias_entity:
                            self.alias_entity.update_value(new_tag)
                        if self.media_type_entity:
                            self.media_type_entity.update_option("playlist")
                else:  # Unmapped tag, clear the fields
                    if self.text_entity:
                        self.text_entity.update_value("")
                    if self.alias_entity:
                        self.alias_entity.update_value("")
                    if self.media_type_entity:
                        self.media_type_entity.update_option("folder")

            # If it's the same tag, decide whether to resume or restart.
            if self.current_tag == new_tag:
                media_player_entity_id = self.config[CONF_MEDIA_PLAYER]
                media_player_state = self.hass.states.get(media_player_entity_id)

                if media_player_state and media_player_state.state == STATE_PAUSED:
                    await self.async_resume_playback()
                else:
                    # If the player is idle, off, or in any other state,
                    # treat it as a new request to play from the beginning.
                    _LOGGER.info("Player is not paused, restarting media for tag %s", new_tag)
                    if new_tag in self.mappings:
                        mapping = self.mappings[new_tag]
                        media_type = mapping.get("type", "playlist")
                        media_name = mapping.get("name")
                        if media_name:
                            if media_type == "folder":
                                await self.async_start_new_folder(media_name)
                            else:
                                await self.async_start_new_playlist(media_name)
            # Otherwise, it's a new media.
            else:
                self.current_tag = new_tag
                if new_tag in self.mappings:
                    mapping = self.mappings[new_tag]
                    if isinstance(mapping, dict):
                        media_type = mapping.get("type", "playlist")
                        media_name = mapping.get("name")
                    else:
                        media_type = "playlist"
                        media_name = mapping

                    if media_name:
                        if media_type == "folder":
                            await self.async_start_new_folder(media_name)
                        else:
                            await self.async_start_new_playlist(media_name)
                    else:
                        _LOGGER.warning("No media name found for tag: %s", new_tag)
                else:
                    _LOGGER.warning("Unmapped tag scanned: %s", new_tag)
        # Tag is removed
        else:
            if self.current_tag:
                media_player_entity_id = self.config[CONF_MEDIA_PLAYER]
                media_player_state = self.hass.states.get(media_player_entity_id)

                # Only pause if the player is currently playing
                if media_player_state and media_player_state.state == STATE_PLAYING:
                    await self.async_pause_player()
                # We don't clear current_tag here, so we can resume it later

    async def async_start_new_playlist(self, playlist_name: str):
        """Start a new playlist from the beginning."""
        _LOGGER.info("Starting new playlist '%s'", playlist_name)
        from homeassistant.exceptions import HomeAssistantError

        try:
            service_data = {
                "entity_id": self.config[CONF_MEDIA_PLAYER],
                "media_id": playlist_name,
                "media_type": "playlist",
            }
            _LOGGER.debug("Calling music_assistant.play_media with data: %s", service_data)
            await self.hass.services.async_call(
                "music_assistant",
                "play_media",
                service_data,
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.error(
                "Error playing playlist '%s': %s. Please ensure the playlist name is spelled correctly and exists in Music Assistant.",
                playlist_name,
                err,
            )

    async def async_start_new_folder(self, folder_name: str):
        """HACK: play a Music Assistant folder now (hardcoded filesystem + device)."""
        from homeassistant.exceptions import HomeAssistantError

        filesystem = self.config.get(CONF_MA_FILESYSTEM)
        if not filesystem:
            _LOGGER.error("Music Assistant filesystem ID is not configured.")
            return

        # Build "<filesystem>://folder/<path>"
        path = str(folder_name).strip().lstrip("/\\").replace("\\", "/")
        media_id = f"{filesystem}://folder/{path}"
        _LOGGER.info("Starting new folder '%s'", media_id)
        try:
            service_data = {
                "entity_id": self.config[CONF_MEDIA_PLAYER],
                "media_id": media_id,
                "media_type": "folder",
            }
            _LOGGER.debug("Calling music_assistant.play_media with data: %s", service_data)
            await self.hass.services.async_call(
                "music_assistant",
                "play_media",
                service_data,
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.error("MA folder play failed (%s): %s", media_id, err)

    async def async_resume_playback(self):
        """Resume the currently paused media player."""
        _LOGGER.info("Resuming playback")
        await self.hass.services.async_call(
            "media_player",
            "media_play",
            {"entity_id": self.config[CONF_MEDIA_PLAYER]},
            blocking=True,
        )

    async def async_pause_player(self):
        """Pause the media player."""
        _LOGGER.info("Pausing player")
        await self.hass.services.async_call(
            "media_player",
            "media_pause",
            {"entity_id": self.config[CONF_MEDIA_PLAYER]},
            blocking=True,
        )

    async def async_map_tag(self, tag_id: str, media_type: str, media_name: str, alias: str = None):
        """Map the currently scanned tag to a media item (used by the 'Map' button)."""
        await _async_map_tag(self.hass, tag_id, media_type, media_name, alias)


