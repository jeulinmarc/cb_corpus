"""Importing this package registers every adapter (generic + bespoke + TOML)."""
from . import base  # noqa: F401  -> registers GenericAdapter for all 63 banks
from . import fed   # noqa: F401  -> overrides "us"
from . import ecb   # noqa: F401  -> overrides "ecb"
from . import boj   # noqa: F401  -> overrides "jp" (A3 minutes + native D1 WPs)
from . import rba   # noqa: F401  -> overrides "au" (A1 decisions)
from . import declarative as _declarative

_declarative.load_toml()

from .base import (  # noqa: E402,F401
    ADAPTERS, INSTANCE_FACTORIES, BankAdapter, GenericAdapter,
    get_adapter, register,
)
