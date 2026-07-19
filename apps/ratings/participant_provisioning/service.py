from django.db import IntegrityError, transaction

from .contracts import (
    ProvisioningError,
    ProvisioningMode,
    ProvisioningOutcome,
    ProvisioningResult,
    ProvisioningState,
)
from .inspection import inspect_snapshot, inspection_error_message
from .operations import bootstrap_participants, reconcile_participants
from .snapshot import load_snapshot


def inspect_provisioning(specifications):
    snapshot = load_snapshot(specifications, lock=False)
    return inspect_snapshot(specifications, snapshot)


def provision_participants(specifications, *, mode=ProvisioningMode.DEFAULT):
    mode = ProvisioningMode(mode)

    if mode == ProvisioningMode.CHECK:
        inspection = inspect_provisioning(specifications)
        if inspection.state != ProvisioningState.EXACT:
            raise ProvisioningError(inspection_error_message(inspection))
        return ProvisioningResult(ProvisioningOutcome.UNCHANGED)

    if mode == ProvisioningMode.DEFAULT:
        inspection = inspect_provisioning(specifications)
        if inspection.state == ProvisioningState.EXACT:
            return ProvisioningResult(ProvisioningOutcome.UNCHANGED)
        if inspection.state != ProvisioningState.EMPTY:
            raise ProvisioningError(inspection_error_message(inspection))

    try:
        with transaction.atomic():
            snapshot = load_snapshot(specifications, lock=True)
            inspection = inspect_snapshot(specifications, snapshot)

            if inspection.state == ProvisioningState.EXACT:
                result = ProvisioningResult(ProvisioningOutcome.UNCHANGED)
            elif inspection.state == ProvisioningState.EMPTY:
                bootstrap_participants(specifications)
                result = ProvisioningResult(ProvisioningOutcome.BOOTSTRAPPED)
            elif mode == ProvisioningMode.RECONCILE and all(
                issue.reconcilable for issue in inspection.issues
            ):
                reconcile_participants(specifications, snapshot)
                result = ProvisioningResult(ProvisioningOutcome.RECONCILED)
            else:
                raise ProvisioningError(inspection_error_message(inspection))
    except IntegrityError as error:
        raise ProvisioningError(
            "동시 실행 또는 데이터 충돌을 감지했습니다. 변경 상태를 확인해 주세요."
        ) from error

    return result
