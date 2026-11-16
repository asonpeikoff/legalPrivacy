"""Unit tests for the community relay torrc generator (offline, no network)."""

import pytest

from noisycricket.relay import (RelayConfigError, RelaySpec, generate_torrc,
                                warn_if_exit_requested)


def test_middle_torrc_rejects_all_exits():
    spec = RelaySpec(mode="middle", nickname="mycricket", or_port=9001)
    torrc = generate_torrc(spec)
    assert "ExitPolicy reject *:*" in torrc
    assert "BridgeRelay 0" in torrc
    assert "ORPort 9001" in torrc
    assert "RelayBandwidthRate 5Mbit" in torrc


def test_bridge_torrc():
    spec = RelaySpec(mode="bridge", nickname="bridgey")
    torrc = generate_torrc(spec)
    assert "BridgeRelay 1" in torrc
    assert "PublishServerDescriptor bridge" in torrc
    assert torrc.endswith("ExitPolicy reject6 *:*\n")


def test_exit_mode_is_refused():
    with pytest.raises(RelayConfigError):
        generate_torrc(RelaySpec(mode="exit"))


def test_or_port_9050_is_refused():
    with pytest.raises(RelayConfigError):
        generate_torrc(RelaySpec(or_port=9050))


def test_nonalphanumeric_nickname_refused():
    with pytest.raises(RelayConfigError):
        generate_torrc(RelaySpec(nickname="bad-nick"))


def test_exit_scanner_defense():
    assert not warn_if_exit_requested(generate_torrc(RelaySpec()))
    torrc = generate_torrc(RelaySpec()) + "ExitPolicy accept *:*\n"
    assert warn_if_exit_requested(torrc)
