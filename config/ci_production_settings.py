"""CI and local-harness settings for strict Django deployment checks."""

import os
from pathlib import Path

from .settings import *  # noqa: F403

# These checks do not fit a Railway-owned shared domain. Keep every other
# deployment warning strict so new production regressions fail CI.
SILENCED_SYSTEM_CHECKS = [
    "security.W005",
    "security.W021",
]

_harness_static_root = os.getenv("HARNESS_STATIC_ROOT")
if _harness_static_root:
    STATIC_ROOT = Path(_harness_static_root)
