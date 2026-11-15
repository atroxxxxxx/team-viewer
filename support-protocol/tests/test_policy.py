import unittest

from support_protocol.policy import text_allowed, validate_input, validate_step


GRANT = {"status": "active", "scope": "pointer,key,text"}


class PolicyTest(unittest.TestCase):
    def test_step_rejects_deletion(self):
        self.assertIsNotNone(validate_step("выполните rm -rf ~/Документы"))

    def test_step_allows_menu(self):
        self.assertIsNone(validate_step("Откройте меню Вид и включите линейку"))

    def test_pointer_inside_frame(self):
        event = {"type": "pointer", "intent": "adjust_app_setting", "pointer": {"x": 10, "y": 10, "button": "left", "action": "click"}}
        self.assertIsNone(validate_input(event, GRANT, 640, 360))

    def test_pointer_outside_frame(self):
        event = {"type": "pointer", "intent": "adjust_app_setting", "pointer": {"x": 700, "y": 10}}
        self.assertIsNotNone(validate_input(event, GRANT, 640, 360))

    def test_shift_delete_blocked(self):
        event = {"type": "key", "intent": "adjust_app_setting", "key": {"code": "Delete", "modifiers": ["shift"], "action": "press"}}
        self.assertIsNotNone(validate_input(event, GRANT, 640, 360))

    def test_text_denylist(self):
        self.assertFalse(text_allowed("sudo passwd root"))
        self.assertFalse(text_allowed("строка\nкоманда"))

    def test_key_code_must_be_plain(self):
        event = {"type": "key", "intent": "adjust_app_setting", "key": {"code": "a;b", "modifiers": [], "action": "press"}}
        self.assertIsNotNone(validate_input(event, GRANT, 640, 360))


if __name__ == "__main__":
    unittest.main()
