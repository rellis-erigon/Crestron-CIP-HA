"""Constants for the Crestron CIP integration."""

DOMAIN = "crestron_cip"

CONF_ADDON_URL = "addon_url"

# The add-on's hostname carries a per-install repository hash, so the URL is
# discovered through Supervisor rather than assumed. This is only the
# fallback shown in the form.
DEFAULT_ADDON_URL = "http://localhost:8099"
DEFAULT_INGRESS_PORT = 8099
ADDON_SLUG_SUFFIX = "crestron-cip"
SUPERVISOR_API = "http://supervisor"

# The event stream carries join changes as they happen; this poll only
# reconciles the join list itself — joins newly exposed, renamed or removed
# in the add-on UI.
RECONCILE_INTERVAL = 30

PLATFORMS = [
    "binary_sensor",
    "button",
    "number",
    "sensor",
    "switch",
    "text",
]

# Analog joins are unsigned 16-bit on the wire.
ANALOG_MAX = 65535
