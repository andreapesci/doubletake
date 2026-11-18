import runpy
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import Mock, call

source = runpy.run_path(str(Path(__file__).with_name("doubletake-tray")), run_name="doubletake_tray_test")
DisplayMode = source["DisplayMode"]
LocalAudio = source["LocalAudio"]
Tray = source["Tray"]
load_settings = source["load_settings"]
default_settings = source["default_settings"]

NATIVE = """Screen 0: minimum 320 x 200, current 2560 x 1600, maximum 16384 x 16384
eDP-1 connected primary 2560x1600+0+0 (normal left inverted right x axis y axis)
   2560x1600     59.97*+
   1920x1080     60.01    59.97
HDMI-1 disconnected (normal left inverted right x axis y axis)
"""
MIRROR = NATIVE.replace("2560x1600+0+0", "1920x1080+0+0").replace(
    "59.97*+", "59.97 +"
).replace("60.01    59.97", "60.01    59.97*")


class DisplayModeTest(unittest.TestCase):
    def test_switches_before_mirroring_and_restores_original_mode_and_rate(self):
        display = DisplayMode()
        display.xrandr = Mock(side_effect=[NATIVE, "", MIRROR, ""])
        display.use_mirror_mode()
        display.use_mirror_mode()
        display.restore()
        display.restore()
        self.assertEqual(display.xrandr.call_args_list, [
            call("--current"),
            call("--output", "eDP-1", "--mode", "1920x1080", "--rate", "59.97"),
            call("--current"),
            call("--output", "eDP-1", "--mode", "2560x1600", "--rate", "59.97"),
        ])
        self.assertIsNone(display.previous)

    def test_preserves_manual_mode_changes(self):
        display = DisplayMode()
        display.xrandr = Mock(side_effect=[NATIVE, "", NATIVE])
        display.use_mirror_mode()
        display.restore()
        self.assertEqual(display.xrandr.call_count, 3)
        self.assertIsNone(display.previous)

    def test_skips_switch_on_a_multiple_monitor_desktop(self):
        display = DisplayMode()
        display.xrandr = Mock(return_value=NATIVE.replace(
            "HDMI-1 disconnected", "HDMI-1 connected 1920x1080+2560+0"
        ))
        display.use_mirror_mode()
        display.xrandr.assert_called_once_with("--current")
        self.assertIsNone(display.previous)

    def test_stale_idle_response_does_not_restore_during_a_connection(self):
        tray = Tray(vaapi_quality_1080p=True)
        tray.checking = True
        tray.command_generation = 1
        tray.restore_display = Mock()
        tray.check_status = Mock()
        tray.status_received({"state": "idle"}, None, 0)
        tray.restore_display.assert_not_called()
        tray.check_status.assert_called_once_with()

    def test_each_open_builds_a_fresh_popup(self):
        # A popup reused across the 2x/1x scale switch of mirror mode reopens
        # partly black, so every open must replace the previous popup.
        popups = [Mock(get_visible=Mock(return_value=False)) for _ in range(2)]
        tray = Tray(vaapi_quality_1080p=True)
        tray.icon = Mock()
        tray.render = Mock()
        tray.refresh = Mock()
        with unittest.mock.patch.dict(Tray.toggle_popup.__globals__, {"ReceiverPopup": Mock(side_effect=popups)}):
            tray.toggle_popup()
            tray.toggle_popup()
        popups[0].destroy.assert_called_once_with()
        popups[1].destroy.assert_not_called()
        popups[1].open_at_icon.assert_called_once_with(tray.icon)
        self.assertIs(tray.popup, popups[1])



class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tray.json"

    def tearDown(self):
        self.directory.cleanup()

    def test_missing_or_invalid_settings_fall_back_to_profile_defaults(self):
        defaults = default_settings(True)
        self.assertEqual(load_settings(self.path, defaults), defaults)
        self.path.write_text('{"fps": 500, "bitrate": "fast", "mute_local": 1}')
        self.assertEqual(load_settings(self.path, defaults), defaults)
        self.path.write_text('{"fps": 30, "bitrate": 10000, "mute_local": false}')
        self.assertEqual(load_settings(self.path, defaults), {"fps": 30, "bitrate": 10000, "mute_local": False})

    def test_daemon_args_carry_settings_and_omit_automatic_bitrate(self):
        tray = Tray(vaapi_quality_1080p=True)
        tray.settings = {"fps": 24, "bitrate": 8000, "mute_local": True}
        self.assertEqual(tray.daemon_args()[-6:], ["-hwaccel", "vaapi-legacy-quality", "-fps", "24", "-bitrate", "8000"])
        tray.settings = {"fps": 30, "bitrate": 0, "mute_local": True}
        self.assertEqual(tray.daemon_args()[-2:], ["-fps", "30"])

    def test_new_settings_wait_for_the_stream_to_end(self):
        tray = Tray(vaapi_quality_1080p=True)
        tray.settings_path = self.path
        tray.render = Mock()
        tray.restart_daemon = Mock()
        tray.daemon = Mock(poll=Mock(return_value=None))
        tray.daemon_settings = (24, 8000)
        tray.streams = [{"device_ip": "192.168.1.2", "state": "streaming"}]
        with unittest.mock.patch.dict(Tray.change_settings.__globals__, {"GLib": Mock()}):
            tray.change_settings({"fps": 30, "bitrate": 10000, "mute_local": True})
        self.assertEqual(load_settings(self.path, {})["bitrate"], 10000)
        tray.apply_settings()
        tray.restart_daemon.assert_not_called()
        self.assertEqual(tray.status_text, "Nuove impostazioni dalla prossima connessione")
        tray.streams = []
        tray.apply_settings()
        tray.restart_daemon.assert_called_once_with()


class LocalAudioTest(unittest.TestCase):
    def test_diverts_to_a_virtual_sink_and_restores_the_speakers(self):
        replies = {
            ("list", "short", "modules"): "7\tmodule-null-sink\tsink_name=other\n",
            ("get-default-sink",): "speakers\n",
            ("load-module", "module-null-sink", "sink_name=doubletake_receiver",
             "sink_properties=device.description=DoubleTake"): "42\n",
            ("list", "short", "sink-inputs"): "5\t1\t2\tPipeWire\ts16le\n",
        }
        audio = LocalAudio()
        audio.pactl = Mock(side_effect=lambda *args: replies.get(args, ""))
        audio.divert()
        audio.divert()
        audio.restore()
        audio.restore()
        self.assertEqual(audio.pactl.call_args_list, [
            call("list", "short", "modules"),
            call("get-default-sink"),
            call("load-module", "module-null-sink", "sink_name=doubletake_receiver",
                 "sink_properties=device.description=DoubleTake"),
            call("set-default-sink", "doubletake_receiver"),
            call("list", "short", "sink-inputs"),
            call("move-sink-input", "5", "doubletake_receiver"),
            call("set-default-sink", "speakers"),
            call("list", "short", "sink-inputs"),
            call("move-sink-input", "5", "speakers"),
            call("unload-module", "42"),
        ])

    def test_removes_a_sink_left_by_a_previous_tray(self):
        audio = LocalAudio()
        audio.pactl = Mock(return_value="9\tmodule-null-sink\tsink_name=doubletake_receiver sink_properties=x\n")
        audio.remove_stale()
        audio.pactl.assert_called_with("unload-module", "9")


if __name__ == "__main__":
    unittest.main()
