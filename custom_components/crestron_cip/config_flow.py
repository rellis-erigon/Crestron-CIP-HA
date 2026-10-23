"""Config flow for Crestron CIP."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
import homeassistant.helpers.config_validation as cv

import os

from .client import CIPClient
from .const import (
    CONF_IPID, CONF_PROJECT_FILE, CONF_SSL, DEFAULT_NAME, DEFAULT_PORT,
    DEFAULT_TLS_PORT, DOMAIN,
)
from .project_loader import load_project


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


class CrestronCipConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
