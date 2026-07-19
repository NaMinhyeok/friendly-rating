from ._participants import get_current_participant
from .auth import login_lockout, login_view, logout_view
from .dashboard import (
    change_score_view,
    get_dashboard_context,
    history_view,
    home,
)
from .push import (
    FID_PATTERN,
    MAX_PUSH_DEVICES_PER_PARTICIPANT,
    register_push_device,
    service_worker,
    unregister_push_device,
)
from .push import _fid_from_json_request as _fid_from_json_request
from .system import health

__all__ = [
    "FID_PATTERN",
    "MAX_PUSH_DEVICES_PER_PARTICIPANT",
    "change_score_view",
    "get_current_participant",
    "get_dashboard_context",
    "health",
    "history_view",
    "home",
    "login_lockout",
    "login_view",
    "logout_view",
    "register_push_device",
    "service_worker",
    "unregister_push_device",
]
