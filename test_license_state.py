import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone


class LicenseStateTests(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["APPDATA"] = self.tempdir.name
        os.environ["DISPLAY_CONTROL_RUNTIME_PROFILE"] = "LicenseStateTests"
        sys.modules.pop("license_meter", None)
        import license_meter

        self.meter = importlib.reload(license_meter)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        self.tempdir.cleanup()
        sys.modules.pop("license_meter", None)

    def _write_raw(self, payload):
        with open(self.meter.LICENSE_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def _read_signature(self):
        with open(self.meter.LICENSE_PATH, encoding="utf-8") as handle:
            return json.load(handle)["signature"]

    def test_round_trip_does_not_mark_a_new_license_tampered(self):
        self.meter.get_status()
        signature = self._read_signature()
        self.meter.get_status()
        self.assertEqual(signature, self._read_signature())
        self.assertFalse(os.path.exists(self.meter.LICENSE_PATH + ".tampered"))

    def test_stale_tamper_marker_is_removed_when_the_license_is_accepted(self):
        self.meter.get_status()
        with open(self.meter.LICENSE_PATH + ".tampered", "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.meter.get_status()
        self.assertFalse(os.path.exists(self.meter.LICENSE_PATH + ".tampered"))

    def test_v1_metered_file_migrates_without_tamper_or_expired_trial(self):
        created_at = 1_700_000_000
        payload = {
            "version": 1,
            "remaining_seconds": 0,
            "lifetime": False,
            "used_seconds": 50,
            "customer_email": "owner@example.com",
            "recovery_code": "",
            "purchase_history": [],
            "restored_codes": [],
            "created_at": created_at,
            "updated_at": created_at,
        }
        payload["signature"] = self.meter._sign_payload(
            payload,
            prefix=self.meter.SIGN_PREFIX_V1,
            fingerprint=self.meter._stable_fingerprint(),
        )
        self._write_raw(payload)

        status = self.meter.get_status()

        self.assertFalse(os.path.exists(self.meter.LICENSE_PATH + ".tampered"))
        self.assertEqual(status["legacy_remaining_seconds"], 0)
        self.assertEqual(status["billing_kind"], "trial")
        self.assertTrue(status["can_protect"])
        self.assertGreater(status["trial_ends_at"], int(time.time()))
        self.assertIn("owner@example.com", status["customer_email"])

    def test_fingerprint_drift_keeps_lifetime_and_does_not_quarantine(self):
        payload = self.meter._new_state()
        payload.pop("signature", None)
        payload["lifetime"] = True
        payload["customer_email"] = "lifetime@example.com"
        payload["signature"] = self.meter._sign_payload(
            {k: v for k, v in payload.items() if k != "signature"},
            prefix=self.meter.SIGN_PREFIX_V2,
            fingerprint="other-pc|other-user|GenuineIntel Family|OTHER-PC",
        )
        self._write_raw(payload)

        status = self.meter.get_status()

        self.assertTrue(status["lifetime"])
        self.assertTrue(status["can_protect"])
        self.assertEqual(status["billing_kind"], "lifetime")
        self.assertFalse(os.path.exists(self.meter.LICENSE_PATH + ".tampered"))
        self.assertTrue(self.meter._verify_state(self.meter.load_or_init_state()))

    def test_missing_signature_is_still_quarantined(self):
        payload = self.meter._new_state()
        payload["lifetime"] = True
        payload.pop("signature", None)
        self._write_raw(payload)

        status = self.meter.get_status()

        self.assertTrue(os.path.exists(self.meter.LICENSE_PATH + ".tampered"))
        self.assertFalse(status["lifetime"])
        self.assertEqual(status["billing_kind"], "trial")
        self.assertTrue(status["can_protect"])

    def test_unreadable_license_starts_a_trial(self):
        with open(self.meter.LICENSE_PATH, "w", encoding="utf-8") as handle:
            handle.write("{not-json")

        status = self.meter.get_status()

        self.assertTrue(os.path.exists(self.meter.LICENSE_PATH + ".unreadable"))
        self.assertFalse(os.path.exists(self.meter.LICENSE_PATH + ".tampered"))
        self.assertTrue(status["trial_active"])
        self.assertTrue(status["can_protect"])

    def test_zero_legacy_credits_do_not_block_an_active_trial(self):
        state = self.meter.load_or_init_state()
        state["legacy_remaining_seconds"] = 0
        state["trial_ends_at"] = int(time.time()) + 86400
        self.meter._write_state(state)

        status = self.meter.get_status()

        self.assertEqual(status["billing_kind"], "trial")
        self.assertTrue(status["can_protect"])
        self.assertNotIn("credit", status["block_reason"])

    def test_legacy_credits_still_work_after_the_trial(self):
        state = self.meter.load_or_init_state()
        state["legacy_remaining_seconds"] = 120
        state["trial_ends_at"] = int(time.time()) - 10
        self.meter._write_state(state)

        status = self.meter.get_status()

        self.assertEqual(status["billing_kind"], "legacy_credits")
        self.assertTrue(status["can_protect"])

    def test_offline_grace_keeps_a_synced_subscription(self):
        period_end = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.meter.sync_remote_account(
            {
                "email": "annual@example.com",
                "recovery_code": "DCPA-1234567890ABCDEF",
                "subscription_status": "active",
                "subscription_current_period_end": period_end,
                "subscription_id": "sub_grace",
                "customer_id": "cus_grace",
                "lifetime": False,
            }
        )
        state = self.meter.load_or_init_state()
        state["trial_ends_at"] = 1
        state["legacy_remaining_seconds"] = 0
        self.meter._write_state(state)

        status = self.meter.get_status()

        self.assertTrue(status["offline_grace"])
        self.assertTrue(status["can_protect"])
        self.assertEqual(status["billing_kind"], "subscription")

    def test_grace_does_not_apply_after_the_window_or_when_canceled(self):
        expired = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.meter.sync_remote_account(
            {
                "email": "annual@example.com",
                "subscription_status": "active",
                "subscription_current_period_end": expired,
                "subscription_id": "sub_old",
                "customer_id": "cus_old",
                "lifetime": False,
            }
        )
        state = self.meter.load_or_init_state()
        state["trial_ends_at"] = 1
        state["legacy_remaining_seconds"] = 0
        self.meter._write_state(state)
        expired_status = self.meter.get_status()
        self.assertFalse(expired_status["offline_grace"])
        self.assertFalse(expired_status["can_protect"])

        recent = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self.meter.load_or_init_state()
        state["subscription_status"] = "canceled"
        state["subscription_current_period_end"] = recent
        state["last_synced_at"] = int(time.time())
        self.meter._write_state(state)
        canceled = self.meter.get_status()
        self.assertFalse(canceled["offline_grace"])
        self.assertFalse(canceled["can_protect"])

    def test_unreachable_server_does_not_clear_a_local_license(self):
        self.meter.activate_lifetime(email="owner@example.com")
        status = self.meter.note_license_server_failure(
            "HTTP error: 404 https://knightlogics.com/api/knight-account"
        )
        self.assertTrue(status["lifetime"])
        self.assertTrue(status["can_protect"])
        self.assertIn("404", status["server_error"])
        self.assertFalse(os.path.exists(self.meter.LICENSE_PATH + ".tampered"))


if __name__ == "__main__":
    unittest.main()
