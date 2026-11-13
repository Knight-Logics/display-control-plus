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

    def test_unverified_signature_does_not_keep_a_forged_lifetime(self):
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

        self.assertFalse(status["lifetime"])
        self.assertEqual(status["billing_kind"], "trial")
        self.assertTrue(status["can_protect"])
        self.assertTrue(os.path.exists(self.meter.LICENSE_PATH + ".tampered"))

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

    def _end_trial(self):
        state = self.meter.load_or_init_state()
        state["trial_ends_at"] = 1
        state["legacy_remaining_seconds"] = 0
        state["lifetime"] = False
        self.meter._write_state(state)

    def test_recent_sync_keeps_an_ended_active_period_for_seven_days(self):
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
        self._end_trial()

        status = self.meter.get_status()

        self.assertTrue(status["offline_grace"])
        self.assertTrue(status["can_protect"])
        self.assertEqual(status["billing_kind"], "subscription")
        self.assertEqual(self.meter.OFFLINE_GRACE_SECONDS, 7 * 24 * 60 * 60)

    def test_active_subscription_lapses_seven_days_after_the_last_sync(self):
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
        state["last_synced_at"] = int(time.time()) - (10 * 24 * 60 * 60)
        self.meter._write_state(state)

        status = self.meter.get_status()

        self.assertFalse(status["offline_grace"])
        self.assertFalse(status["can_protect"])

    def test_canceled_subscription_stays_entitled_until_period_end_plus_seven_days(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.meter.sync_remote_account(
            {
                "email": "annual@example.com",
                "subscription_status": "canceled",
                "subscription_current_period_end": recent,
                "subscription_id": "sub_cancel",
                "lifetime": False,
            }
        )
        self._end_trial()
        status = self.meter.get_status()
        self.assertTrue(status["offline_grace"])
        self.assertTrue(status["can_protect"])

        older = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = self.meter.load_or_init_state()
        state["subscription_current_period_end"] = older
        self.meter._write_state(state)
        ended = self.meter.get_status()
        self.assertFalse(ended["offline_grace"])
        self.assertFalse(ended["can_protect"])

    def test_http_503_fail_open_keeps_a_paid_license_and_does_not_slide(self):
        self.meter.activate_lifetime(email="owner@example.com")
        outage = {
            "ok": False,
            "fail_open": True,
            "http_status": 503,
            "error": "License store is temporarily unavailable.",
            "offline_grace_seconds": 7 * 24 * 60 * 60,
        }
        first = self.meter.apply_remote_sync_result(outage)
        until = self.meter.load_or_init_state()["fail_open_until"]
        second = self.meter.apply_remote_sync_result(outage)

        self.assertTrue(first["lifetime"])
        self.assertTrue(second["can_protect"])
        self.assertEqual(self.meter.load_or_init_state()["fail_open_until"], until)
        self.assertGreater(until, int(time.time()))

    def test_http_503_fail_open_does_not_revive_an_expired_trial(self):
        state = self.meter.load_or_init_state()
        state["trial_ends_at"] = 1
        state["lifetime"] = False
        state["subscription_status"] = "inactive"
        state["legacy_remaining_seconds"] = 0
        self.meter._write_state(state)

        status = self.meter.apply_remote_sync_result(
            {
                "ok": False,
                "fail_open": True,
                "http_status": 503,
                "error": "License store is temporarily unavailable.",
                "offline_grace_seconds": 7 * 24 * 60 * 60,
            }
        )

        self.assertFalse(status["can_protect"])
        self.assertFalse(self.meter.load_or_init_state().get("fail_open_paid"))

    def test_successful_sync_clears_fail_open_and_a_revoked_lifetime(self):
        self.meter.activate_lifetime(email="owner@example.com")
        self.meter.apply_remote_sync_result(
            {
                "ok": False,
                "fail_open": True,
                "http_status": 503,
                "error": "License store is temporarily unavailable.",
                "offline_grace_seconds": 7 * 24 * 60 * 60,
            }
        )
        self.assertTrue(self.meter.load_or_init_state().get("fail_open_paid"))
        self.meter.sync_remote_account(
            {
                "email": "owner@example.com",
                "lifetime": False,
                "subscription_status": "canceled",
                "subscription_current_period_end": "2020-01-01T00:00:00Z",
                "subscription_id": "sub_old",
            }
        )
        state = self.meter.load_or_init_state()
        state["trial_ends_at"] = 1
        state["legacy_remaining_seconds"] = 0
        self.meter._write_state(state)
        status = self.meter.get_status()
        self.assertFalse(status["lifetime"])
        self.assertFalse(status["can_protect"])
        self.assertFalse(self.meter.load_or_init_state().get("fail_open_paid"))

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
