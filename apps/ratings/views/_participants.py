from django.core.exceptions import PermissionDenied

from ..models import Participant


def get_current_participant(request):
    try:
        return request.user.participant
    except Participant.DoesNotExist as error:
        raise PermissionDenied("참가자만 이용할 수 있습니다.") from error
