import importlib
import json
import logging
import os
import sys
import tempfile
import time
import threading
import unittest


class AttrDict(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class FakeStripe:
    def __init__(self):
        self.api_key = ""
        self.sessions = {}
        self.subscriptions = {}
        self.portal_urls = []
        self.checkout = type("CheckoutNamespace", (), {})()
        self.checkout.Session = type("CheckoutSessionAPI", (), {})()
        self.checkout.Session.create = self._create_session
        self.checkout.Session.retrieve = self._retrieve_session
        self.Subscription = type("SubscriptionAPI", (), {})()
        self.Subscription.retrieve = self._retrieve_subscription
        self.billing_portal = type("BillingPortalNamespace", (), {})()
        self.billing_portal.Session = type("BillingPortalSessionAPI", (), {})()
        self.billing_portal.Session.create = self._create_portal_session
        self.Webhook = type("WebhookAPI", (), {})()
        self.Webhook.construct_event = self._construct_event

    def _create_session(self, **kwargs):
        session_id = f"cs_test_{len(self.sessions) + 1}"
        customer_id = kwargs.get("customer") or f"cus_test_{len(self.sessions) + 1}"
        session = AttrDict(
            id=session_id,
            url=f"https://checkout.test/{session_id}",
            mode=kwargs.get("mode", "payment"),
            metadata=dict(kwargs.get("metadata", {})),
            customer=customer_id,
            customer_email=kwargs.get("customer_email") or kwargs.get("client_reference_id") or kwargs.get("metadata", {}).get("email", ""),
            payment_status="paid",
            subscription="",
        )
        if kwargs.get("mode") == "subscription":
            subscription_id = f"sub_test_{len(self.subscriptions) + 1}"
            current_period_end = int(time.time()) + 31536000
            self.subscriptions[subscription_id] = AttrDict(
                id=subscription_id,
                customer=customer_id,
                status="active",
                current_period_end=current_period_end,
                cancel_at_period_end=False,
            )
            session["subscription"] = subscription_id
        self.sessions[session_id] = session
        return session

    def _retrieve_session(self, session_id):
        return self.sessions[session_id]

    def _retrieve_subscription(self, subscription_id):
        return self.subscriptions[subscription_id]

    def _create_portal_session(self, customer, return_url):
        url = f"https://billing.test/{customer}"
        self.portal_urls.append(url)
        return AttrDict(url=url, customer=customer, return_url=return_url)

    def _construct_event(self, payload, signature, secret):
        del signature, secret
        return json.loads(payload.decode("utf-8"))


class BillingServerTests(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["DCP_LICENSE_LEDGER_FILE"] = os.path.join(self.tempdir.name, "license_ledger.json")
        os.environ["DCP_LICENSE_SERVER_LOG"] = os.path.join(self.tempdir.name, "license_server.log")
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_local"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_local"
        os.environ["STRIPE_SUCCESS_URL_DISPLAY_CONTROL"] = "http://127.0.0.1:8200/success"
        os.environ["STRIPE_CANCEL_URL_DISPLAY_CONTROL"] = "http://127.0.0.1:8200/cancel"
        os.environ["STRIPE_BILLING_PORTAL_RETURN_URL_DISPLAY_CONTROL"] = "https://knightlogics.com"

        sys.modules.pop("license_server", None)
        import license_server

        self.license_server = importlib.reload(license_server)
        self.fake_stripe = FakeStripe()
        self.license_server.stripe = self.fake_stripe
        self.client = self.license_server.app.test_client()

    def tearDown(self):
        logging.shutdown()
        for handler in list(logging.getLogger().handlers):
            try:
                handler.close()
            except Exception:
                pass
            try:
                logging.getLogger().removeHandler(handler)
            except Exception:
                pass
        os.environ.clear()
        os.environ.update(self._old_env)
        self.tempdir.cleanup()
        sys.modules.pop("license_server", None)


class PaymentClientIntegrationTests(unittest.TestCase):
    def setUp(self):
        from werkzeug.serving import make_server

        self._old_env = dict(os.environ)
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["DCP_LICENSE_LEDGER_FILE"] = os.path.join(self.tempdir.name, "license_ledger.json")
        os.environ["DCP_LICENSE_SERVER_LOG"] = os.path.join(self.tempdir.name, "license_server.log")
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_local"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_local"
        os.environ["STRIPE_SUCCESS_URL_DISPLAY_CONTROL"] = "http://127.0.0.1:8200/success"
        os.environ["STRIPE_CANCEL_URL_DISPLAY_CONTROL"] = "http://127.0.0.1:8200/cancel"
        os.environ["STRIPE_BILLING_PORTAL_RETURN_URL_DISPLAY_CONTROL"] = "https://knightlogics.com"

        sys.modules.pop("license_server", None)
        import license_server

        self.license_server = importlib.reload(license_server)
        self.fake_stripe = FakeStripe()
        self.license_server.stripe = self.fake_stripe
        self.client = self.license_server.app.test_client()

        self.http_server = make_server("127.0.0.1", 0, self.license_server.app)
        self.port = self.http_server.socket.getsockname()[1]
        self.server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.server_thread.start()

        os.environ["DISPLAY_CONTROL_LICENSE_SERVER"] = f"http://127.0.0.1:{self.port}"
        sys.modules.pop("payment_service", None)
        import payment_service

        self.payment_service = importlib.reload(payment_service)

    def tearDown(self):
        try:
            self.http_server.shutdown()
            self.server_thread.join(timeout=5)
        except Exception:
            pass
        logging.shutdown()
        for handler in list(logging.getLogger().handlers):
            try:
                handler.close()
            except Exception:
                pass
            try:
                logging.getLogger().removeHandler(handler)
            except Exception:
                pass
        os.environ.clear()
        os.environ.update(self._old_env)
        self.tempdir.cleanup()
        sys.modules.pop("license_server", None)
        sys.modules.pop("payment_service", None)

    def test_payment_client_round_trip_over_http(self):
        created = self.payment_service.create_checkout_session("annual", "client@example.com")
        self.assertTrue(created.get("ok"), created)
        self.assertIn("session_id", created)

        confirmed = self.payment_service.confirm_session(created["session_id"])
        self.assertTrue(confirmed.get("ok"), confirmed)
        account = confirmed["account"]
        self.assertTrue(account["subscription_active"])

        restored = self.payment_service.restore_account_remote(account["email"], account["recovery_code"])
        self.assertTrue(restored.get("ok"), restored)

        portal = self.payment_service.create_portal_session(account["email"], account["recovery_code"])
        self.assertTrue(portal.get("ok"), portal)
        self.assertIn("https://billing.test/", portal.get("url", ""))

    def _checkout_and_confirm(self, plan_key, email="tester@example.com"):
        created = self.client.post(
            "/api/licenses/create-checkout-session",
            json={"email": email, "plan_key": plan_key},
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        created_payload = created.get_json()
        self.assertTrue(created_payload.get("ok"))
        confirmed = self.client.post(
            "/api/licenses/confirm-session",
            json={"session_id": created_payload["session_id"]},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.get_json())
        confirmed_payload = confirmed.get_json()
        self.assertTrue(confirmed_payload.get("ok"))
        return confirmed_payload

    def test_annual_checkout_confirm_restore_and_portal(self):
        confirmed_payload = self._checkout_and_confirm("annual")
        account = confirmed_payload["account"]
        self.assertTrue(account["subscription_active"])
        self.assertEqual(account["plan_key"], "annual")
        self.assertTrue(account["recovery_code"].startswith("DCPA-"))

        restored = self.client.post(
            "/api/licenses/restore",
            json={"email": account["email"], "recovery_code": account["recovery_code"]},
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        restored_payload = restored.get_json()
        self.assertTrue(restored_payload["subscription_active"])
        self.assertEqual(restored_payload["subscription_status"], "active")

        portal = self.client.post(
            "/api/licenses/create-portal-session",
            json={"email": account["email"], "recovery_code": account["recovery_code"]},
        )
        self.assertEqual(portal.status_code, 200, portal.get_json())
        portal_payload = portal.get_json()
        self.assertTrue(portal_payload.get("ok"))
        self.assertIn("https://billing.test/", portal_payload.get("url", ""))

    def test_lifetime_checkout_sets_lifetime_entitlement(self):
        confirmed_payload = self._checkout_and_confirm("lifetime", email="lifetime@example.com")
        account = confirmed_payload["account"]
        self.assertTrue(account["lifetime"])
        self.assertEqual(account["plan_key"], "lifetime")
        self.assertFalse(account["subscription_active"])

    def test_subscription_deleted_webhook_disables_subscription(self):
        confirmed_payload = self._checkout_and_confirm("annual", email="cancelme@example.com")
        account = confirmed_payload["account"]
        subscription_id = account["subscription_id"]
        customer_id = account["customer_id"]

        event = {
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": subscription_id,
                    "customer": customer_id,
                    "status": "canceled",
                    "current_period_end": int(time.time()),
                    "cancel_at_period_end": False,
                }
            },
        }
        response = self.client.post(
            "/api/licenses/stripe-webhook",
            data=json.dumps(event).encode("utf-8"),
            headers={"Stripe-Signature": "test"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.get_json())

        restored = self.client.post(
            "/api/licenses/restore",
            json={"email": account["email"], "recovery_code": account["recovery_code"]},
        )
        restored_payload = restored.get_json()
        self.assertFalse(restored_payload["subscription_active"])
        self.assertEqual(restored_payload["subscription_status"], "canceled")


class LicenseMeterTests(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["APPDATA"] = self.tempdir.name
        os.environ["DISPLAY_CONTROL_RUNTIME_PROFILE"] = "BillingTestProfile"

        sys.modules.pop("license_meter", None)
        import license_meter

        self.license_meter = importlib.reload(license_meter)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        self.tempdir.cleanup()
        sys.modules.pop("license_meter", None)

    def test_new_install_starts_with_trial(self):
        status = self.license_meter.get_status()
        self.assertTrue(status["trial_active"])
        self.assertTrue(status["can_protect"])
        self.assertEqual(status["billing_kind"], "trial")

    def test_sync_remote_account_activates_subscription_without_metering(self):
        synced = self.license_meter.sync_remote_account(
            {
                "ok": True,
                "email": "annual@example.com",
                "recovery_code": "DCPA-1234567890ABCDEF",
                "subscription_active": True,
                "subscription_status": "active",
                "subscription_current_period_end": "2030-01-01T00:00:00Z",
                "subscription_cancel_at_period_end": False,
                "subscription_id": "sub_test_123",
                "customer_id": "cus_test_123",
                "lifetime": False,
            }
        )
        self.assertTrue(synced["subscription_active"])
        self.assertEqual(synced["billing_kind"], "subscription")

        after_consume = self.license_meter.consume_active_seconds(600)
        self.assertTrue(after_consume["subscription_active"])
        self.assertEqual(after_consume["billing_kind"], "subscription")


if __name__ == "__main__":
    unittest.main(verbosity=2)