import unittest

from ensure_overlay_bg_task import register_per_user_startup


class _Result:
    def __init__(self, returncode, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


class StartupRegistrationTests(unittest.TestCase):
    def test_access_denied_still_enables_the_per_user_run_key(self):
        calls = []

        def runner(cmd, **_kwargs):
            calls.append(list(cmd))
            if cmd[0] == "reg":
                return _Result(0)
            return _Result(1, stderr="ERROR: Access is denied.")

        result = register_per_user_startup(r'"C:\Program Files\Display Control\tray.exe"', runner=runner)

        self.assertTrue(result["ok"])
        self.assertTrue(result["run_key"])
        self.assertFalse(result["scheduled_task"])
        self.assertIn("Access is denied", result["task_error"])
        create = next(cmd for cmd in calls if cmd[:3] == ["schtasks", "/Create", "/F"])
        self.assertIn("/RL", create)
        self.assertIn("LIMITED", create)
        self.assertIn("/IT", create)
        self.assertNotIn("HIGHEST", create)
        run_key = next(cmd for cmd in calls if cmd[0] == "reg")
        self.assertEqual(run_key[1], "add")
        self.assertIn(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", run_key)

    def test_startup_fails_only_when_the_run_key_and_task_both_fail(self):
        def runner(cmd, **_kwargs):
            if cmd[0] == "reg":
                return _Result(1, stderr="ERROR: Access is denied.")
            return _Result(1, stderr="ERROR: Access is denied.")

        result = register_per_user_startup(r'"C:\app\tray.exe"', runner=runner)

        self.assertFalse(result["ok"])
        self.assertFalse(result["run_key"])
        self.assertFalse(result["scheduled_task"])


if __name__ == "__main__":
    unittest.main()
