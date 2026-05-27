from __future__ import annotations

import json
import logging
import os
import secrets
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from flask import Flask, jsonify, request

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

try:
    import stripe as _stripe
except Exception:
    _stripe = None

from billing_catalog import PLAN_CATALOG, get_plan


APP_ROOT = Path(__file__).resolve().parent
WORKSPACE = APP_ROOT / "server_runtime"
WORKSPACE.mkdir(parents=True, exist_ok=True)
LEDGER_PATH = Path(os.environ.get("DCP_LICENSE_LEDGER_FILE", str(WORKSPACE / "license_ledger.json")))
LOG_PATH = Path(os.environ.get("DCP_LICENSE_SERVER_LOG", str(WORKSPACE / "license_server.log")))

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_SUCCESS_URL = os.environ.get("STRIPE_SUCCESS_URL_DISPLAY_CONTROL", "http://127.0.0.1:8200/success")
STRIPE_CANCEL_URL = os.environ.get("STRIPE_CANCEL_URL_DISPLAY_CONTROL", "http://127.0.0.1:8200/cancel")
STRIPE_BILLING_PORTAL_RETURN_URL = os.environ.get("STRIPE_BILLING_PORTAL_RETURN_URL_DISPLAY_CONTROL", "https://knightlogics.com")
STRIPE_CURRENCY = os.environ.get("STRIPE_CURRENCY", "usd").strip().lower()
APP_NAME = os.environ.get("APP_NAME", "Display Control+")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "").replace(" ", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
SMTP_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS and SMTP_FROM)

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due"}

stripe = _stripe
if stripe is not None and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_to_iso(epoch_value):
    try:
        epoch_int = int(epoch_value)
    except Exception:
        return ""
    return datetime.fromtimestamp(epoch_int, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _stripe_ready():
    return stripe is not None and bool(STRIPE_SECRET_KEY)


class LicenseLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"accounts": {}, "processed_sessions": []})

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            data = {"accounts": {}, "processed_sessions": []}
        if not isinstance(data.get("accounts"), dict):
            data["accounts"] = {}
        if not isinstance(data.get("processed_sessions"), list):
            data["processed_sessions"] = []
        return data

    def _write(self, payload):
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _decorate_account(self, account, email):
        now = utc_now()
        account.setdefault("email", str(email or "").strip().lower())
        account.setdefault("recovery_code", "")
        account.setdefault("recovery_codes", [])
        account.setdefault("customer_id", "")
        account.setdefault("subscription_id", "")
        account.setdefault("subscription_status", "inactive")
        account.setdefault("subscription_current_period_end", "")
        account.setdefault("subscription_cancel_at_period_end", False)
        account.setdefault("subscription_active", False)
        account.setdefault("lifetime", False)
        account.setdefault("payments", [])
        account.setdefault("created_at", now)
        account.setdefault("updated_at", now)
        return account

    def _find_account(self, payload, email="", customer_id="", subscription_id=""):
        accounts = payload.setdefault("accounts", {})
        email_key = str(email or "").strip().lower()
        if email_key and isinstance(accounts.get(email_key), dict):
            return accounts[email_key]
        for account in accounts.values():
            if not isinstance(account, dict):
                continue
            if customer_id and str(account.get("customer_id", "")).strip() == str(customer_id).strip():
                return account
            if subscription_id and str(account.get("subscription_id", "")).strip() == str(subscription_id).strip():
                return account
        return None

    def lookup_account(self, email):
        payload = self._read()
        account = self._find_account(payload, email=email)
        if not isinstance(account, dict):
            return None
        return self._public_account(account)

    def _ensure_account(self, payload, email, customer_id=""):
        email_key = str(email or "").strip().lower()
        accounts = payload.setdefault("accounts", {})
        account = self._find_account(payload, email=email_key, customer_id=customer_id)
        if not isinstance(account, dict):
            account = {}
            accounts[email_key] = account
        account = self._decorate_account(account, email_key)
        if customer_id:
            account["customer_id"] = str(customer_id).strip()
        accounts[email_key] = account
        return account

    def create_recovery_code(self):
        return "DCPA-" + secrets.token_hex(8).upper()

    def _ensure_recovery_code(self, account):
        recovery_code = str(account.get("recovery_code", "")).strip().upper()
        if not recovery_code:
            recovery_code = self.create_recovery_code()
            account["recovery_code"] = recovery_code
        codes = [str(code).strip().upper() for code in account.get("recovery_codes", []) if str(code).strip()]
        if recovery_code not in codes:
            codes.append(recovery_code)
        account["recovery_codes"] = codes
        return recovery_code

    def _plan_label_for_account(self, account):
        if bool(account.get("lifetime", False)):
            return get_plan("lifetime").get("label", "Lifetime License")
        if bool(account.get("subscription_active", False)) or str(account.get("subscription_id", "")).strip():
            return get_plan("annual").get("label", "Annual Subscription")
        return ""

    def _public_account(self, account):
        recovery_code = self._ensure_recovery_code(account)
        if bool(account.get("lifetime", False)):
            plan_key = "lifetime"
        elif bool(account.get("subscription_active", False)) or str(account.get("subscription_id", "")).strip():
            plan_key = "annual"
        else:
            plan_key = ""
        return {
            "ok": True,
            "email": str(account.get("email", "")).strip().lower(),
            "recovery_code": recovery_code,
            "lifetime": bool(account.get("lifetime", False)),
            "subscription_active": bool(account.get("subscription_active", False)),
            "subscription_status": str(account.get("subscription_status", "inactive")).strip() or "inactive",
            "subscription_current_period_end": str(account.get("subscription_current_period_end", "")).strip(),
            "subscription_cancel_at_period_end": bool(account.get("subscription_cancel_at_period_end", False)),
            "subscription_id": str(account.get("subscription_id", "")).strip(),
            "customer_id": str(account.get("customer_id", "")).strip(),
            "plan_key": plan_key,
            "plan_label": self._plan_label_for_account(account),
        }

    def record_checkout(
        self,
        email,
        plan_key,
        session_id,
        checkout_mode,
        customer_id="",
        subscription_id="",
        subscription_status="inactive",
        subscription_current_period_end="",
        payment_status="",
    ):
        payload = self._read()
        processed = payload.setdefault("processed_sessions", [])
        account = self._ensure_account(payload, email, customer_id)
        recovery_code = self._ensure_recovery_code(account)

        if session_id in processed:
            self._write(payload)
            return {
                "already_processed": True,
                "account": self._public_account(account),
                "recovery_code": recovery_code,
            }

        plan = get_plan(plan_key)
        if not plan:
            raise ValueError(f"Unsupported plan: {plan_key}")

        account.setdefault("payments", []).append(
            {
                "session_id": str(session_id).strip(),
                "plan_key": plan.get("key"),
                "checkout_mode": str(checkout_mode or plan.get("checkout_mode", "payment")).strip(),
                "price_cents": int(plan.get("price_cents", 0)),
                "customer_id": str(customer_id or account.get("customer_id", "")).strip(),
                "subscription_id": str(subscription_id or "").strip(),
                "payment_status": str(payment_status or "").strip(),
                "purchased_at": utc_now(),
            }
        )
        if customer_id:
            account["customer_id"] = str(customer_id).strip()

        if plan.get("key") == "lifetime":
            account["lifetime"] = True
        elif plan.get("key") == "annual":
            account["subscription_id"] = str(subscription_id or account.get("subscription_id", "")).strip()
            account["subscription_status"] = str(subscription_status or account.get("subscription_status", "inactive")).strip() or "inactive"
            account["subscription_current_period_end"] = str(subscription_current_period_end or account.get("subscription_current_period_end", "")).strip()
            account["subscription_active"] = account["subscription_status"] in ACTIVE_SUBSCRIPTION_STATUSES

        account["updated_at"] = utc_now()
        processed.append(str(session_id).strip())
        self._write(payload)
        return {
            "already_processed": False,
            "account": self._public_account(account),
            "recovery_code": recovery_code,
        }

    def update_subscription_state(self, customer_id="", subscription_id="", status="inactive", current_period_end="", cancel_at_period_end=False):
        payload = self._read()
        account = self._find_account(payload, customer_id=customer_id, subscription_id=subscription_id)
        if not isinstance(account, dict):
            return None

        if customer_id:
            account["customer_id"] = str(customer_id).strip()
        if subscription_id:
            account["subscription_id"] = str(subscription_id).strip()
        account["subscription_status"] = str(status or "inactive").strip() or "inactive"
        account["subscription_current_period_end"] = str(current_period_end or "").strip()
        account["subscription_cancel_at_period_end"] = bool(cancel_at_period_end)
        account["subscription_active"] = account["subscription_status"] in ACTIVE_SUBSCRIPTION_STATUSES
        account["updated_at"] = utc_now()
        self._ensure_recovery_code(account)
        self._write(payload)
        return self._public_account(account)

    def restore_account(self, email, recovery_code):
        payload = self._read()
        account = self._find_account(payload, email=email)
        if not isinstance(account, dict):
            return {"ok": False, "error": "Account not found."}
        self._ensure_recovery_code(account)
        entered_code = str(recovery_code or "").strip().upper()
        valid_codes = {str(code).strip().upper() for code in account.get("recovery_codes", []) if str(code).strip()}
        if entered_code not in valid_codes:
            return {"ok": False, "error": "Recovery code does not match this account."}
        self._write(payload)
        return self._public_account(account)


ledger = LicenseLedger(LEDGER_PATH)
app = Flask(__name__)


def send_recovery_email(to_email, recovery_code, plan_label):
    if not SMTP_CONFIGURED:
        return {"ok": False, "error": "SMTP is not configured."}
    subject = f"{APP_NAME} Purchase Confirmation & Recovery Code"
    body = (
        f"Thank you for your purchase of {plan_label}.\n\n"
        f"Your account recovery code:\n{recovery_code}\n\n"
        f"Use this in Display Control+ via Restore Account to sync your subscription or restore your lifetime license on another machine.\n\n"
        f"Keep this code private. It acts like account proof for billing restore and account sync."
    )
    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        logging.info("Recovery email sent to %s", to_email)
        return {"ok": True}
    except Exception as exc:
        logging.error("Recovery email send failed for %s: %s", to_email, exc)
        return {"ok": False, "error": str(exc)}


def _build_line_items(plan):
    price_data = {
        "currency": STRIPE_CURRENCY,
        "product_data": {
            "name": plan.get("label"),
            "description": plan.get("description", ""),
        },
        "unit_amount": int(plan.get("price_cents", 0)),
    }
    if plan.get("checkout_mode") == "subscription":
        price_data["recurring"] = {"interval": plan.get("interval", "year")}
    return [{"price_data": price_data, "quantity": 1}]


def _sync_checkout_session(session):
    metadata = session.get("metadata", {}) or {}
    plan_key = str(metadata.get("plan_key", "")).strip().lower()
    plan = get_plan(plan_key)
    email = str(metadata.get("email", session.get("customer_email", ""))).strip().lower()
    if not plan or not email:
        return {"ok": False, "error": "Checkout session metadata is incomplete."}

    customer_id = str(session.get("customer", "")).strip()
    session_id = str(session.get("id", "")).strip()
    payment_status = str(session.get("payment_status", "")).strip()

    if plan.get("checkout_mode") == "subscription":
        subscription_id = str(session.get("subscription", "")).strip()
        if not subscription_id or stripe is None:
            return {"ok": False, "error": "Subscription checkout did not return a subscription id."}
        subscription = stripe.Subscription.retrieve(subscription_id)
        subscription_status = str(subscription.get("status", "inactive")).strip() or "inactive"
        if subscription_status not in ACTIVE_SUBSCRIPTION_STATUSES:
            return {"ok": False, "error": f"Subscription is not active yet ({subscription_status})."}
        result = ledger.record_checkout(
            email=email,
            plan_key=plan_key,
            session_id=session_id,
            checkout_mode=plan.get("checkout_mode"),
            customer_id=customer_id,
            subscription_id=subscription_id,
            subscription_status=subscription_status,
            subscription_current_period_end=epoch_to_iso(subscription.get("current_period_end")),
            payment_status=payment_status,
        )
    else:
        if payment_status != "paid":
            return {"ok": False, "error": f"Checkout session is not paid yet ({payment_status or 'unknown'})."}
        result = ledger.record_checkout(
            email=email,
            plan_key=plan_key,
            session_id=session_id,
            checkout_mode=plan.get("checkout_mode"),
            customer_id=customer_id,
            payment_status=payment_status,
        )

    send_result = send_recovery_email(email, result.get("recovery_code", ""), plan.get("label", plan_key.title()))
    return {
        "ok": True,
        "already_processed": bool(result.get("already_processed", False)),
        "email_sent": bool(send_result.get("ok", False)),
        "email_error": send_result.get("error", ""),
        "account": result.get("account", {}),
    }


@app.get("/api/licenses/health")
def health():
    return jsonify(
        {
            "ok": True,
            "stripe_configured": bool(STRIPE_SECRET_KEY),
            "stripe_module_loaded": bool(stripe is not None),
            "smtp_configured": SMTP_CONFIGURED,
            "plans": list(PLAN_CATALOG.keys()),
        }
    )


@app.post("/api/licenses/create-checkout-session")
def create_checkout_session():
    if not _stripe_ready():
        return jsonify({"ok": False, "error": "Stripe is not configured on the license server."}), 503

    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    plan_key = str(payload.get("plan_key", "")).strip().lower()
    plan = get_plan(plan_key)
    if not plan:
        return jsonify({"ok": False, "error": "Invalid plan."}), 400
    if not email:
        return jsonify({"ok": False, "error": "Email is required."}), 400

    session_kwargs = {
        "mode": plan.get("checkout_mode", "payment"),
        "success_url": STRIPE_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": STRIPE_CANCEL_URL,
        "allow_promotion_codes": True,
        "line_items": _build_line_items(plan),
        "metadata": {
            "product": "display_control_plus",
            "plan_key": plan.get("key"),
            "email": email,
        },
        "client_reference_id": email,
    }

    existing_account = ledger.lookup_account(email)
    customer_id = str((existing_account or {}).get("customer_id", "")).strip()
    if customer_id:
        session_kwargs["customer"] = customer_id
    else:
        session_kwargs["customer_email"] = email

    try:
        session = stripe.checkout.Session.create(**session_kwargs)
    except Exception as exc:
        logging.error("Failed to create checkout session for %s (%s): %s", email, plan_key, exc)
        return jsonify({"ok": False, "error": f"Could not create checkout session: {exc}"}), 400

    logging.info("Created %s checkout session %s for %s", plan_key, session.id, email)
    return jsonify({"ok": True, "url": session.url, "session_id": session.id, "plan_key": plan_key})


@app.post("/api/licenses/confirm-session")
def confirm_session():
    if not _stripe_ready():
        return jsonify({"ok": False, "error": "Stripe is not configured on the license server."}), 503

    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        return jsonify({"ok": False, "error": "session_id is required."}), 400

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:
        logging.error("Failed to retrieve session %s: %s", session_id, exc)
        return jsonify({"ok": False, "error": f"Could not verify checkout session: {exc}"}), 400

    result = _sync_checkout_session(session)
    if not result.get("ok", False):
        return jsonify(result), 409
    return jsonify(result)


@app.post("/api/licenses/create-portal-session")
def create_portal_session():
    if not _stripe_ready():
        return jsonify({"ok": False, "error": "Stripe is not configured on the license server."}), 503

    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    recovery_code = str(payload.get("recovery_code", "")).strip()
    if not email or not recovery_code:
        return jsonify({"ok": False, "error": "email and recovery_code are required."}), 400

    account_result = ledger.restore_account(email, recovery_code)
    if not account_result.get("ok", False):
        return jsonify(account_result), 404

    customer_id = str(account_result.get("customer_id", "")).strip()
    if not customer_id:
        return jsonify({"ok": False, "error": "No Stripe customer is attached to this account yet."}), 409

    try:
        portal = stripe.billing_portal.Session.create(customer=customer_id, return_url=STRIPE_BILLING_PORTAL_RETURN_URL)
    except Exception as exc:
        logging.error("Failed to create billing portal session for %s: %s", email, exc)
        return jsonify({"ok": False, "error": f"Could not create billing portal session: {exc}"}), 400

    logging.info("Created billing portal session for %s", email)
    return jsonify({"ok": True, "url": portal.url})


@app.post("/api/licenses/stripe-webhook")
def stripe_webhook():
    if not _stripe_ready() or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "STRIPE_WEBHOOK_SECRET is not configured."}), 503

    payload = request.get_data(as_text=False)
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid payload"}), 400
    except Exception:
        return jsonify({"ok": False, "error": "Invalid signature"}), 400

    event_type = str(event.get("type", "")).strip()
    logging.info("Stripe webhook received: %s", event_type)

    if event_type == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        try:
            _sync_checkout_session(session)
        except Exception as exc:
            logging.error("Webhook checkout sync failed: %s", exc)
    elif event_type in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        subscription = event.get("data", {}).get("object", {})
        ledger.update_subscription_state(
            customer_id=str(subscription.get("customer", "")).strip(),
            subscription_id=str(subscription.get("id", "")).strip(),
            status=str(subscription.get("status", "inactive")).strip() or "inactive",
            current_period_end=epoch_to_iso(subscription.get("current_period_end")),
            cancel_at_period_end=bool(subscription.get("cancel_at_period_end", False)),
        )

    return jsonify({"ok": True, "received": True})


@app.post("/api/licenses/restore")
def restore_account():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    recovery_code = str(payload.get("recovery_code", "")).strip()
    if not email or not recovery_code:
        return jsonify({"ok": False, "error": "email and recovery_code are required."}), 400
    result = ledger.restore_account(email, recovery_code)
    if not result.get("ok", False):
        return jsonify(result), 404
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8787, debug=False)
