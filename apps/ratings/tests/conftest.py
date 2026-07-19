import pytest

from .factories import ParticipantPair, create_participant_pair


@pytest.fixture
def participant_pair(db) -> ParticipantPair:
    return create_participant_pair()
