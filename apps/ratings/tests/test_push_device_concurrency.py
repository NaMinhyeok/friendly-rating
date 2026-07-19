from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from ..models import Participant, PushDevice
from ..services import register_participant_push_device
from .factories import create_participant_pair


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentPushDeviceRegistrationTests(TransactionTestCase):
    def setUp(self):
        self.first, self.second, _, _ = create_participant_pair()
        self.first_fids = [f"c{'A' * 20}{index}" for index in range(5)]
        self.second_fids = [f"d{'B' * 20}{index}" for index in range(5)]
        for fid in self.first_fids:
            PushDevice.objects.create(
                participant=self.first,
                firebase_installation_id=fid,
            )
        for fid in self.second_fids:
            PushDevice.objects.create(
                participant=self.second,
                firebase_installation_id=fid,
            )

    def test_cross_owner_fid_swaps_do_not_deadlock_at_the_device_limit(self):
        start = Barrier(3)

        def register_device(participant_id: int, fid: str) -> None:
            close_old_connections()
            try:
                participant = Participant.objects.get(pk=participant_id)
                start.wait(timeout=5)
                register_participant_push_device(
                    participant=participant,
                    firebase_installation_id=fid,
                )
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(
                    register_device,
                    self.first.pk,
                    self.second_fids[0],
                ),
                executor.submit(
                    register_device,
                    self.second.pk,
                    self.first_fids[0],
                ),
            )
            start.wait(timeout=5)
            for future in futures:
                future.result(timeout=10)

        self.assertTrue(
            PushDevice.objects.filter(
                participant=self.first,
                firebase_installation_id=self.second_fids[0],
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            PushDevice.objects.filter(
                participant=self.second,
                firebase_installation_id=self.first_fids[0],
                is_active=True,
            ).exists()
        )
        self.assertLessEqual(
            PushDevice.objects.filter(
                participant=self.first,
                is_active=True,
            ).count(),
            5,
        )
        self.assertLessEqual(
            PushDevice.objects.filter(
                participant=self.second,
                is_active=True,
            ).count(),
            5,
        )
