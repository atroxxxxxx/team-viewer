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
