import pytest
from django.urls import Resolver404, resolve, reverse


@pytest.mark.parametrize(
    ("url_name", "public_path"),
    (("logout", "/logout/"),),
)
def test_mutation_url_names_and_paths_are_stable(url_name, public_path):
    assert reverse(url_name) == public_path
    assert resolve(public_path).url_name == url_name


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
