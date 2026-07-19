from dataclasses import dataclass
from enum import StrEnum


class ProvisioningError(Exception):
    """Raised when participant provisioning cannot proceed safely."""


class ProvisioningMode(StrEnum):
    DEFAULT = "default"
    CHECK = "check"
    RECONCILE = "reconcile"


class ProvisioningState(StrEnum):
    EMPTY = "empty"
    EXACT = "exact"
    DRIFT = "drift"
    UNSAFE_DRIFT = "unsafe_drift"


class ProvisioningOutcome(StrEnum):
    BOOTSTRAPPED = "bootstrapped"
    UNCHANGED = "unchanged"
    RECONCILED = "reconciled"


class PasswordStatus(StrEnum):
    EXACT = "exact"
    MISMATCH = "mismatch"
    OUTDATED_HASH = "outdated_hash"


@dataclass(frozen=True)
class ParticipantSpec:
    slot: int
    username: str
    display_name: str
    pin: str


@dataclass(frozen=True)
class DriftIssue:
    reconcilable: bool
    message: str


@dataclass(frozen=True)
class Inspection:
    state: ProvisioningState
    issues: tuple[DriftIssue, ...]


@dataclass(frozen=True)
class ProvisioningResult:
    outcome: ProvisioningOutcome
