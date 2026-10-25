"""Constants for the Crestron CIP integration."""
from __future__ import annotations

DOMAIN = "crestron_cip"

CONF_IPID = "ipid"
CONF_SSL = "ssl"
CONF_PROJECT_FILE = "project_file"
# CONF_HOST, CONF_PORT, CONF_NAME come from homeassistant.const

DEFAULT_PORT = 41794
DEFAULT_TLS_PORT = 41796
DEFAULT_NAME = "Crestron"

PLATFORMS = ["binary_sensor", "sensor", "button"]

# dispatcher signals
SIGNAL_CONN = f"{DOMAIN}_conn_{{entry}}"              # connectivity changed
SIGNAL_NEW = f"{DOMAIN}_new_{{entry}}"                # (kind, join) new feedback join seen
SIGNAL_VALUE = f"{DOMAIN}_val_{{entry}}_{{kind}}_{{join}}"  # value update for one join

# services
SERVICE_PRESS = "press"
SERVICE_SET_DIGITAL = "set_digital"
SERVICE_SET_ANALOG = "set_analog"
SERVICE_SET_SERIAL = "set_serial"
SERVICE_REQUEST_UPDATE = "request_update"

ATTR_JOIN = "join"
ATTR_VALUE = "value"
ATTR_DURATION = "duration"
ATTR_ENTRY_ID = "entry_id"
