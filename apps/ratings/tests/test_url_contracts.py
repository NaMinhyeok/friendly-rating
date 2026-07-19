import pytest
from django.urls import Resolver404, resolve, reverse


@pytest.mark.parametrize(
    ("url_name", "public_path"),
    (("logout", "/logout/"),),
)
def test_mutation_url_names_and_paths_are_stable(url_name, public_path):
    assert reverse(url_name) == public_path
    assert resolve(public_path).url_name == url_name


def test_score_change_thread_url_name_and_path_are_stable():
    public_path = reverse(
        "score-change-thread",
        kwargs={"score_change_id": 42},
    )

    assert public_path == "/history/42/"
    assert resolve(public_path).url_name == "score-change-thread"


@pytest.mark.parametrize(
    "retired_path",
    (
        "/score/change/",
        "/notifications/devices/register/",
        "/notifications/devices/unregister/",
    ),
)
def test_retired_unversioned_mutations_are_not_routed(retired_path):
    with pytest.raises(Resolver404):
        resolve(retired_path)
