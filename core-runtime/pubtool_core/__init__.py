"""Stable desktop execution SDK. No literature-domain imports belong here."""
CORE_VERSION = "0.4.0"
CORE_API_VERSION = 1
BOOTSTRAP_API_VERSION = 1
CAPABILITIES = frozenset({"rpc", "native.save_file", "native.clipboard_html", "native.open_url",
                          "lifecycle", "webchannel", "rendering", "package.extensions"})
