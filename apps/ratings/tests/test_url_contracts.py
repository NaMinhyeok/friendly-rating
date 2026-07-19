import pytest
from django.urls import resolve, reverse


@pytest.mark.parametrize(
    ("url_name", "public_path"),
    (
        ("logout", "/logout/"),
        ("change-score", "/score/change/"),
        ("register-push-device", "/notifications/devices/register/"),
        ("unregister-push-device", "/notifications/devices/unregister/"),
    ),
)
def test_mutation_url_names_and_paths_are_stable(url_name, public_path):
    assert reverse(url_name) == public_path
    assert resolve(public_path).url_name == url_name
