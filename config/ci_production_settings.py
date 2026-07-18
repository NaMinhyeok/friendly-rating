"""CI-only settings for strict Django deployment checks."""

from .settings import *  # noqa: F403

# These checks do not fit a Railway-owned shared domain. Keep every other
# deployment warning strict so new production regressions fail CI.
SILENCED_SYSTEM_CHECKS = [
    "security.W005",
    "security.W021",
]
