from __future__ import annotations

import pytest

from src.services import HostnamePolicy


@pytest.mark.parametrize(
    "server_name,expected",
    [
        ("example.org", "example.org"),
        ("Example.Org", "example.org"),
        ("host.example.org:443", "host.example.org"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("[2001:db8::1]:8448", "2001:db8::1"),
        ("", ""),
        ("  ", ""),
    ],
)
def test_hostname_from_server_name(server_name, expected):
    assert HostnamePolicy.hostname_from_server_name(server_name) == expected


def test_server_name_from_mxid():
    assert HostnamePolicy.server_name_from_mxid("@bot:example.org") == "example.org"
    assert HostnamePolicy.server_name_from_mxid("not-an-mxid") is None


def test_is_allowed_hostname_exact_and_subdomain():
    allowed = "example.org"
    assert HostnamePolicy.is_allowed_hostname("example.org", allowed)
    assert HostnamePolicy.is_allowed_hostname("sub.example.org", allowed)
    assert not HostnamePolicy.is_allowed_hostname("evilexample.org", allowed)
    assert not HostnamePolicy.is_allowed_hostname("other.com", allowed)
    assert not HostnamePolicy.is_allowed_hostname("", allowed)


def test_no_allowlist_allows_all():
    assert HostnamePolicy.is_allowed_hostname("anything.com", None)
    assert HostnamePolicy.is_allowed_hostname("", None)
