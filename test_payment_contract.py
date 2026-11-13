import importlib
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock


class _Response:
    def __init__(self, payload, status=200):
        self._raw = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class PaymentContractTests(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        os.environ["DISPLAY_CONTROL_LICENSE_SERVER"] = "https://license.test"
        sys.modules.pop("payment_service", None)
        import payment_service

        self.payment = importlib.reload(payment_service)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        sys.modules.pop("payment_service", None)

    def test_default_server_is_knightlogics_and_blank_disables_it(self):
        os.environ.pop("DISPLAY_CONTROL_LICENSE_SERVER", None)
        self.assertEqual(self.payment.license_server_base_url(), "https://knightlogics.com")
        os.environ["DISPLAY_CONTROL_LICENSE_SERVER"] = ""
        self.assertEqual(self.payment.license_server_base_url(), "")
        self.assertFalse(self.payment.is_payment_available())

    def test_knight_status_period_end_becomes_iso_and_grace_maps_to_past_due(self):
        account = self.payment.normalize_account_payload(
            {
                "ok": True,
                "email": "Owner@Example.com",
                "lifetime": False,
                "subscription_active": True,
                "in_grace": True,
                "period_end": 1_800_000_000,
                "grace_until": 1_800_000_000 + 7 * 24 * 60 * 60,
                "offline_grace_seconds": 604800,
                "entitlement_token": "kl1.payload.mac",
            }
        )
        self.assertEqual(account["email"], "owner@example.com")
        self.assertEqual(account["subscription_status"], "past_due")
        self.assertTrue(account["subscription_current_period_end"].endswith("Z"))
        self.assertEqual(account["entitlement_token"], "kl1.payload.mac")
        self.assertEqual(account["offline_grace_seconds"], 604800)

    def test_sync_calls_both_routes_and_keeps_the_restore_grant(self):
        def urlopen(req, timeout=0):
            del timeout
            if req.full_url.endswith("/api/licenses/restore"):
                return _Response(
                    {
                        "ok": True,
                        "email": "owner@example.com",
                        "recovery_code": "DCPA-1",
                        "lifetime": True,
                        "subscription_status": "inactive",
                    }
                )
            if req.full_url.endswith("/api/knight-account"):
                body = json.loads(req.data.decode("utf-8"))
                self.assertEqual(body["action"], "status")
                self.assertEqual(body["product_id"], "displaycontrol")
                return _Response(
                    {
                        "ok": True,
                        "email": "owner@example.com",
                        "lifetime": False,
                        "subscription_active": False,
                        "entitlement_token": "kl1.payload.mac",
                        "offline_grace_seconds": 604800,
                    }
                )
            raise AssertionError(req.full_url)

        with mock.patch.object(self.payment.urllib.request, "urlopen", urlopen):
            result = self.payment.sync_account("owner@example.com", "DCPA-1")

        self.assertTrue(result["ok"])
        self.assertTrue(result["lifetime"])
        self.assertEqual(result["entitlement_token"], "kl1.payload.mac")
        self.assertFalse(result["fail_open"])

    def test_status_alone_does_not_grant_when_restore_fails(self):
        def urlopen(req, timeout=0):
            del timeout
            if req.full_url.endswith("/api/licenses/restore"):
                raise urllib.error.HTTPError(
                    req.full_url,
                    404,
                    "missing",
                    hdrs=None,
                    fp=io.BytesIO(json.dumps({"ok": False, "error": "Account not found."}).encode("utf-8")),
                )
            return _Response({"ok": True, "email": "owner@example.com", "lifetime": True, "subscription_active": True})

        with mock.patch.object(self.payment.urllib.request, "urlopen", urlopen):
            result = self.payment.sync_account("owner@example.com", "DCPA-1")

        self.assertFalse(result["ok"])
        self.assertFalse(result.get("lifetime"))
        self.assertEqual(result["http_status"], 404)
        self.assertFalse(result["fail_open"])

    def test_explicit_503_fail_open_is_kept_and_stripe_503_is_not(self):
        def outage(req, timeout=0):
            del timeout
            payload = {"ok": False, "error": "License store is temporarily unavailable.", "fail_open": True, "offline_grace_seconds": 604800}
            raise urllib.error.HTTPError(req.full_url, 503, "unavailable", hdrs=None, fp=io.BytesIO(json.dumps(payload).encode("utf-8")))

        with mock.patch.object(self.payment.urllib.request, "urlopen", outage):
            result = self.payment.sync_account("owner@example.com", "DCPA-1")
        self.assertTrue(result["fail_open"])
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(result["offline_grace_seconds"], 604800)

        def stripe_down(req, timeout=0):
            del timeout
            payload = {"ok": False, "error": "Stripe is not configured on the license server."}
            raise urllib.error.HTTPError(req.full_url, 503, "unavailable", hdrs=None, fp=io.BytesIO(json.dumps(payload).encode("utf-8")))

        with mock.patch.object(self.payment.urllib.request, "urlopen", stripe_down):
            result = self.payment.create_checkout_session("annual", "owner@example.com")
        self.assertFalse(result["fail_open"])
        self.assertEqual(result["http_status"], 503)


if __name__ == "__main__":
    unittest.main()
