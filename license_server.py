from __future__ import annotations

import json
import os
import secrets
import smtplib
from datetime import datetime
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

import stripe

APP_ROOT = Path(__file__).resolve().parent
WORKSPACE = APP_ROOT / "server_runtime"
WORKSPACE.mkdir(parents=True, exist_ok=True)
LEDGER_PATH = Path(os.environ.get("DCP_LICENSE_LEDGER_FILE", str(WORKSPACE / "license_ledger.json")))

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SUCCESS_URL = os.environ.get("STRIPE_SUCCESS_URL_DISPLAY_CONTROL", "http://127.0.0.1:8787/payment/success")
STRIPE_CANCEL_URL = os.environ.get("STRIPE_CANCEL_URL_DISPLAY_CONTROL", "http://127.0.0.1:8787/payment/cancel")
STRIPE_CURRENCY = os.environ.get("STRIPE_CURRENCY", "usd")
APP_NAME = os.environ.get("APP_NAME", "Display Control+")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "").replace(" ", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
SMTP_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS and SMTP_FROM)

stripe.api_key = STRIPE_SECRET_KEY

PRICE_MAP = {
    "hours_500": {
        "label": "Display Control+ 500 Hours",
        "unit_amount": 500,
        "hours": 500,
        "type": "hours",
    },
    "hours_1200": {
        "label": "Display Control+ 1200 Hours",
        "unit_amount": 1000,
        "hours": 1200,
        "type": "hours",
    },
    "lifetime": {
        "label": "Display Control+ Lifetime License",
        "unit_amount": 2999,
        "hours": 0,
        "type": "lifetime",
    },
}


def utc_now():
    return datetime.utcnow().isoformat() + "Z"


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
        if "accounts" not in data:
            data["accounts"] = {}
        if "processed_sessions" not in data:
            data["processed_sessions"] = []
        return data

    def _write(self, payload):
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _ensure_account(self, payload, email):
        key = email.strip().lower()
        accounts = payload.setdefault("accounts", {})
        account = accounts.get(key)
        if not isinstance(account, dict):
            account = {
                "email": key,
                "recovery_codes": [],
                "total_hours_purchased": 0,
                "lifetime": False,
                "sessions": [],
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            accounts[key] = account
        return account

    def create_recovery_code(self):
        return "DCPA-" + secrets.token_hex(8).upper()

    def has_processed_session(self, session_id):
        payload = self._read()
        return session_id in payload.get("processed_sessions", [])

    def record_purchase(self, email, plan_key, session_id):
        payload = self._read()
        processed = payload.setdefault("processed_sessions", [])
        if session_id in processed:
            account = self._ensure_account(payload, email)
            self._write(payload)
            return {
                "already_processed": True,
                "account": account,
                "recovery_code": account.get("recovery_codes", [""])[-1] if account.get("recovery_codes") else "",
            }

        account = self._ensure_account(payload, email)
        plan = PRICE_MAP[plan_key]
        recovery_code = self.create_recovery_code()
        account.setdefault("recovery_codes", []).append(recovery_code)
        account.setdefault("sessions", []).append({
            "session_id": session_id,
            "plan_key": plan_key,
            "type": plan["type"],
            "hours": plan["hours"],
            "recovery_code": recovery_code,
            "purchased_at": utc_now(),
        })
        if plan["type"] == "lifetime":
            account["lifetime"] = True
        else:
            account["total_hours_purchased"] = int(account.get("total_hours_purchased", 0)) + int(plan["hours"])
        account["updated_at"] = utc_now()
        processed.append(session_id)
        self._write(payload)
        return {
            "already_processed": False,
            "account": account,
            "recovery_code": recovery_code,
        }

    def restore_account(self, email, recovery_code):
        payload = self._read()
        account = payload.get("accounts", {}).get(email.strip().lower())
        if not isinstance(account, dict):
            return {"ok": False, "error": "Account not found."}
        codes = {str(c).strip().upper() for c in account.get("recovery_codes", [])}
        if str(recovery_code).strip().upper() not in codes:
            return {"ok": False, "error": "Recovery code does not match this account."}
        return {
            "ok": True,
            "email": account.get("email", ""),
            "lifetime": bool(account.get("lifetime", False)),
            "total_hours_purchased": int(account.get("total_hours_purchased", 0)),
            "recovery_codes": list(account.get("recovery_codes", [])),
            "sessions": list(account.get("sessions", [])),
        }


ledger = LicenseLedger(LEDGER_PATH)
app = Flask(__name__)


def send_recovery_email(to_email, recovery_code, plan_label):
    if not SMTP_CONFIGURED:
        return {"ok": False, "error": "SMTP is not configured."}
    subject = f"{APP_NAME} Purchase Confirmation & Recovery Code"
    body = (
        f"Thank you for your purchase of {plan_label}.\n\n"
        f"Your recovery code:\n{recovery_code}\n\n"
        f"Use this in Display Control+ via Restore Account if you reinstall or move devices.\n"
    )
    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/licenses/create-checkout-session")
def create_checkout_session():
    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe is not configured on server."}), 503

    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    plan_key = str(payload.get("plan_key", "")).strip()
    if plan_key not in PRICE_MAP:
        return jsonify({"error": "Invalid plan."}), 400
    if not email:
        return jsonify({"error": "Email is required."}), 400

    plan = PRICE_MAP[plan_key]
    session = stripe.checkout.Session.create(
        mode="payment",
        success_url=STRIPE_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=STRIPE_CANCEL_URL,
        customer_email=email,
        line_items=[
            {
                "price_data": {
                    "currency": STRIPE_CURRENCY,
                    "product_data": {
                        "name": plan["label"],
                    },
                    "unit_amount": plan["unit_amount"],
                },
                "quantity": 1,
            }
        ],
        metadata={
            "product": "display_control_plus",
            "plan_key": plan_key,
            "email": email,
        },
    )
    return jsonify({"url": session.url, "session_id": session.id})


@app.post("/api/licenses/confirm-session")
def confirm_session():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        return jsonify({"error": "session_id is required."}), 400

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:
        return jsonify({"error": f"Could not verify checkout session: {exc}"}), 400

    if session.get("payment_status") != "paid":
        return jsonify({"error": "Checkout session is not paid yet."}), 409

    metadata = session.get("metadata", {})
    plan_key = str(metadata.get("plan_key", "")).strip()
    email = str(metadata.get("email", session.get("customer_email", ""))).strip().lower()
    if plan_key not in PRICE_MAP or not email:
        return jsonify({"error": "Session metadata is incomplete."}), 400

    result = ledger.record_purchase(email, plan_key, session_id)
    recovery_code = result.get("recovery_code", "")
    send_result = send_recovery_email(email, recovery_code, PRICE_MAP[plan_key]["label"])

    account = result.get("account", {})
    return jsonify({
        "ok": True,
        "already_processed": bool(result.get("already_processed", False)),
        "email": email,
        "plan_key": plan_key,
        "recovery_code": recovery_code,
        "lifetime": bool(account.get("lifetime", False)),
        "total_hours_purchased": int(account.get("total_hours_purchased", 0)),
        "email_sent": bool(send_result.get("ok", False)),
        "email_error": send_result.get("error", ""),
    })


@app.post("/api/licenses/stripe-webhook")
def stripe_webhook():
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "STRIPE_WEBHOOK_SECRET is not configured."}), 503

    payload = request.get_data(as_text=False)
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except Exception:
        return jsonify({"error": "Invalid signature"}), 400

    if event.get("type") == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        plan_key = str(metadata.get("plan_key", "")).strip()
        email = str(metadata.get("email", session.get("customer_email", ""))).strip().lower()
        if plan_key in PRICE_MAP and email:
            result = ledger.record_purchase(email, plan_key, session.get("id", ""))
            recovery_code = result.get("recovery_code", "")
            send_recovery_email(email, recovery_code, PRICE_MAP[plan_key]["label"])

    return jsonify({"received": True})


@app.post("/api/licenses/restore")
def restore_account():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    recovery_code = str(payload.get("recovery_code", "")).strip()
    if not email or not recovery_code:
        return jsonify({"error": "email and recovery_code are required."}), 400
    result = ledger.restore_account(email, recovery_code)
    if not result.get("ok", False):
        return jsonify(result), 404
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8787, debug=False)
