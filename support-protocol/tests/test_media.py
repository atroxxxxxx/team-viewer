import unittest

from support_protocol.media import media_action, should_open_player


class MediaActionTests(unittest.TestCase):
    def test_idle_until_tunnel(self):
        self.assertEqual(media_action("", "", "queued"), "idle")

    def test_start_when_tunnel_appears(self):
        self.assertEqual(media_action("", "tunnel-a", "assigned"), "start")

    def test_keep_same_tunnel(self):
        self.assertEqual(media_action("tunnel-a", "tunnel-a", "assigned"), "keep")

    def test_switch_tunnel(self):
        self.assertEqual(media_action("tunnel-a", "tunnel-b", "assigned"), "start")

    def test_stop_when_resolved(self):
        self.assertEqual(media_action("tunnel-a", "tunnel-a", "resolved"), "stop")
        self.assertEqual(media_action("", "tunnel-a", "resolved"), "idle")


class PlayerTests(unittest.TestCase):
    def test_open_until_process_runs(self):
        self.assertTrue(should_open_player("", "tunnel-a", False, "assigned"))
        self.assertFalse(should_open_player("tunnel-a", "tunnel-a", True, "assigned"))

    def test_reopen_after_exit_or_new_tunnel(self):
        self.assertTrue(should_open_player("tunnel-a", "tunnel-a", False, "remote_control"))
        self.assertTrue(should_open_player("tunnel-a", "tunnel-b", True, "assigned"))

    def test_closed_ticket_does_not_play(self):
        self.assertFalse(should_open_player("", "tunnel-a", False, "resolved"))
        self.assertFalse(should_open_player("", "", False, "queued"))


class PipelineTests(unittest.TestCase):
    def test_encoding_limits_and_keyframes(self):
        from unittest.mock import patch
        from support_protocol.media import publish_args
        with patch("support_protocol.media.ffmpeg_path", return_value="ffmpeg"):
            for mode in ("screen", "camera", "testsrc"):
                args = publish_args("rtsp://localhost/stream", mode, ":0", fps=20, crf=25)
                self.assertEqual(args[args.index("-g") + 1], "20")
                self.assertEqual(args[args.index("-keyint_min") + 1], "20")
                self.assertEqual(args[args.index("-crf") + 1], "25")
                self.assertIn("zerolatency", args)
                self.assertIn("2M", args)
                self.assertEqual(args[-1], "rtsp://localhost/stream")

    def test_decoder_input_flags_and_no_scale(self):
        from unittest.mock import patch
        from support_protocol.media import play_args
        with patch("support_protocol.media.ffmpeg_path", return_value="ffmpeg"):
            args = play_args("rtsp://localhost/stream")
        for option in ("-probesize", "-analyzeduration", "-fflags", "-flags", "-timeout"):
            self.assertLess(args.index(option), args.index("-i"))
        self.assertNotIn("-vf", args)
        self.assertEqual(args[-1], "-")

    def test_discard_old_frames_keep_partial_frame(self):
        from support_protocol.media import latest_frame
        buffer = bytearray()
        self.assertIsNone(latest_frame(buffer, b"aa", 3))
        self.assertEqual(latest_frame(buffer, b"abbbcccdd", 3), b"ccc")
        self.assertEqual(buffer, b"dd")
        self.assertEqual(latest_frame(buffer, b"d", 3), b"ddd")
        self.assertEqual(buffer, b"")
