"""Tests for core/audio.py: microphone device selection and diagnostics.

Covers the fix for voice mode failing with zero visible error when the
system's default input device isn't the one the user actually talks into:
MIC_DEVICE resolution, the active device name surfaced back to callers, and
the diagnostic message built when a device turns out to be producing no
signal at all.
"""
import sys
from types import ModuleType, SimpleNamespace

from core.audio import Microphone, list_input_devices, no_signal_message, resolve_device

DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2},
    {"name": "Microphone (4- USB Audio Device)", "max_input_channels": 1},
    {"name": "Microphone (Realtek(R) Audio)", "max_input_channels": 2},
    {"name": "Speakers (Realtek(R) Audio)", "max_input_channels": 0},  # output-only
]


def _fake_sounddevice(devices, input_stream=None):
    """query_devices() mirrors the real dual signature: no args returns the
    full list, a specific index returns that one device's dict."""
    module = ModuleType("sounddevice")

    def query_devices(idx=None):
        return devices if idx is None else devices[idx]

    module.query_devices = query_devices
    module.default = SimpleNamespace(device=(0, 0))
    module.InputStream = input_stream or (lambda **kwargs: SimpleNamespace(start=lambda: None))  # noqa: ARG005
    return module


# -- resolve_device -----------------------------------------------------------

class TestResolveDevice:
    def test_blank_is_system_default(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))
        assert resolve_device("") is None

    def test_auto_is_system_default(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))
        assert resolve_device("auto") is None

    def test_numeric_index_is_used_directly_without_touching_sounddevice(self):
        # No sounddevice faked at all -- a numeric index must resolve
        # without needing to enumerate devices.
        assert resolve_device("2") == 2

    def test_name_substring_matches_case_insensitively(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))
        assert resolve_device("realtek") == 2

    def test_unmatched_name_falls_back_to_default_rather_than_raising(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))
        assert resolve_device("nonexistent device") is None

    def test_output_only_devices_are_never_matched(self, monkeypatch):
        """max_input_channels == 0 devices must be skipped even on a name hit."""
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))
        assert resolve_device("speakers") is None

    def test_sounddevice_unavailable_falls_back_to_default(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", None)
        assert resolve_device("realtek") is None


# -- list_input_devices / no_signal_message -----------------------------------

class TestListInputDevices:
    def test_lists_only_input_capable_devices_with_index(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))
        assert list_input_devices() == [
            "[0] Microsoft Sound Mapper - Input",
            "[1] Microphone (4- USB Audio Device)",
            "[2] Microphone (Realtek(R) Audio)",
        ]

    def test_returns_empty_list_rather_than_raising_when_unavailable(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", None)
        assert list_input_devices() == []


class TestNoSignalMessage:
    def test_names_the_device_and_lists_alternatives(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))
        message = no_signal_message("Microphone (4- USB Audio Device)")
        assert "Microphone (4- USB Audio Device)" in message
        assert "MIC_DEVICE" in message
        assert "Realtek" in message

    def test_handles_a_missing_device_name(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice([]))
        assert "the selected device" in no_signal_message(None)


# -- Microphone.device_name ----------------------------------------------------

class TestMicrophoneDeviceName:
    def test_resolved_from_an_explicit_device_index(self, monkeypatch):
        captured = {}

        def input_stream(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(start=lambda: None)

        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES, input_stream))
        monkeypatch.setitem(sys.modules, "numpy", ModuleType("numpy"))

        mic = Microphone(device=2)
        mic.start()

        assert mic.device_name == "Microphone (Realtek(R) Audio)"
        assert captured["device"] == 2  # InputStream was actually told which device to open

    def test_falls_back_to_the_system_default_when_device_is_none(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", _fake_sounddevice(DEVICES))
        monkeypatch.setitem(sys.modules, "numpy", ModuleType("numpy"))

        mic = Microphone()  # device=None
        mic.start()

        assert mic.device_name == DEVICES[0]["name"]  # fake default.device == (0, 0)

    def test_naming_failure_does_not_break_stream_start(self, monkeypatch):
        fake_sd = _fake_sounddevice(DEVICES)

        def boom(idx=None):  # noqa: ARG001
            raise RuntimeError("gone")

        fake_sd.query_devices = boom
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
        monkeypatch.setitem(sys.modules, "numpy", ModuleType("numpy"))

        mic = Microphone()
        mic.start()  # must not raise even though naming the device failed

        assert mic.device_name is None
