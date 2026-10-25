"""Config flow for Crestron CIP."""
from __future__ import annotations

from typing import Any

import os
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import FileSelector, FileSelectorConfig, TextSelector

from .client import CIPClient
from .const import (
    CONF_IPID, CONF_PROJECT_FILE, CONF_SSL, DEFAULT_NAME, DEFAULT_PORT,
    DEFAULT_TLS_PORT, DOMAIN,
)
from .panel_store import OPT_PANEL_NAME, import_from_file
from .project_loader import load_project

# The options form: the panel as an upload, or a file already on the box.
PANEL_UPLOAD = "panel_file"
PANEL_PATH = "panel_path"
PANEL_ACCEPT = ".zip,.c3p"
ALLOWED_ROOTS = ("/share", "/media")


def _validate_project(path: str) -> bool:
    try:
        return os.path.exists(path) and len(load_project(path)) > 0
    except Exception:  # noqa: BLE001
        return False


async def _try_connect(host: str, ipid: int, port: int | None, use_ssl: bool) -> str | None:
    """Return None on success, or an error key on failure."""
    client = CIPClient(host, ipid, port=port, use_ssl=use_ssl)
    try:
        await client.connect(timeout=12.0)
    except Exception:  # noqa: BLE001
        return "cannot_connect"
    finally:
        await client.close()
    return None


async def _import_panel(hass: HomeAssistant, name: str, upload: str | None,
                        path: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Import the panel the form was given: (options, None) or (None, error key)."""
    from homeassistant.components.file_upload import process_uploaded_file

    from .panel.importer import PanelImportError

    def run() -> dict[str, Any]:
        if upload:
            with process_uploaded_file(hass, upload) as file_path:
                # The upload is deleted when this block ends; import_from_file
                # keeps its own copy.
                return import_from_file(hass, Path(file_path), name)
        return import_from_file(hass, Path(path), name)

    if path and not upload:
        resolved = Path(path).expanduser().resolve()
        roots = [Path(hass.config.config_dir), *map(Path, ALLOWED_ROOTS)]
        if not any(resolved.is_relative_to(root) for root in roots):
            return None, "panel_outside"
        if not resolved.is_file():
            return None, "panel_missing"
    try:
        return await hass.async_add_executor_job(run), None
    except PanelImportError:
        return None, "panel_unreadable"
    except (OSError, ValueError):
        return None, "panel_missing"


class CrestronCipOptionsFlow(OptionsFlow):
    """Configure: give the processor its panel, as a file."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.config_entry
        current = entry.options.get(OPT_PANEL_NAME)
        if user_input is not None:
            upload = user_input.get(PANEL_UPLOAD)
            path = (user_input.get(PANEL_PATH) or "").strip() or None
            if not upload and not path:
                # Nothing new: keep the panel there is.
                return self.async_create_entry(data=dict(entry.options))
            name = entry.data.get(CONF_NAME) or entry.title
            options, error = await _import_panel(self.hass, name, upload, path)
            if error:
                errors["base"] = error
            else:
                options.pop("screens", None)
                return self.async_create_entry(data={**entry.options, **options})

        schema = vol.Schema({
            vol.Optional(PANEL_UPLOAD): FileSelector(FileSelectorConfig(accept=PANEL_ACCEPT)),
            vol.Optional(PANEL_PATH): TextSelector(),
        })
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors,
            description_placeholders={"current": current or "none yet"})


class CrestronCipConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CrestronCipOptionsFlow:
        return CrestronCipOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            use_ssl = user_input[CONF_SSL]
            port = user_input.get(CONF_PORT) or (DEFAULT_TLS_PORT if use_ssl else DEFAULT_PORT)
            try:
                ipid = int(str(user_input[CONF_IPID]), 0)  # accepts "3" or "0x03"
            except ValueError:
                ipid = -1
            project_file = (user_input.get(CONF_PROJECT_FILE) or "").strip()
            if not (1 <= ipid <= 0xFE):
                errors[CONF_IPID] = "invalid_ipid"
            elif project_file and not await self.hass.async_add_executor_job(_validate_project, project_file):
                errors[CONF_PROJECT_FILE] = "bad_project"
            else:
                uid = f"{user_input[CONF_HOST]}-{ipid:02x}"
                await self.async_set_unique_id(uid)
                self._abort_if_unique_id_configured()
                err = await _try_connect(user_input[CONF_HOST], ipid, port, use_ssl)
                if err:
                    errors["base"] = err
                else:
                    return self.async_create_entry(
                        title=f"{user_input.get(CONF_NAME, DEFAULT_NAME)} ({user_input[CONF_HOST]} / IPID {ipid:02X})",
                        data={CONF_HOST: user_input[CONF_HOST], CONF_IPID: ipid,
                              CONF_PORT: port, CONF_SSL: use_ssl,
                              CONF_NAME: user_input.get(CONF_NAME, DEFAULT_NAME),
                              CONF_PROJECT_FILE: project_file},
                    )

        schema = vol.Schema({
            vol.Required(CONF_HOST): cv.string,
            vol.Required(CONF_IPID, default="0x03"): cv.string,
            vol.Required(CONF_SSL, default=False): cv.boolean,
            vol.Optional(CONF_PORT): cv.port,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_PROJECT_FILE, default=""): cv.string,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
