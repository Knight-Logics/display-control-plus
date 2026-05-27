"""HTTP client for the hosted Display Control+ billing service."""

import json
import logging
import os
import urllib.error
import urllib.request
import webbrowser

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from billing_catalog import get_plan

if load_dotenv is not None:
    load_dotenv()


LICENSE_SERVER_BASE_URL = os.environ.get("DISPLAY_CONTROL_LICENSE_SERVER", "").strip().rstrip("/")
LICENSE_SERVER_TIMEOUT_SEC = max(1.0, float(os.environ.get("LICENSE_SERVER_TIMEOUT_SEC", "6")))


def is_payment_available():
    return bool(LICENSE_SERVER_BASE_URL)


def _request_json(path, payload=None, method="POST"):
    if not LICENSE_SERVER_BASE_URL:
        return {"ok": False, "error": "License server is not configured."}

    url = f"{LICENSE_SERVER_BASE_URL}{path}"
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=LICENSE_SERVER_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}
        if "ok" not in payload:
            payload["ok"] = False
        payload.setdefault("error", f"HTTP error: {exc.code}")
        logging.warning("Billing request failed: %s %s", path, payload.get("error", "unknown error"))
        return payload
    except Exception as exc:
        logging.error("Billing request exception for %s: %s", path, exc)
        return {"ok": False, "error": f"License server error: {exc}"}


def create_checkout_session(plan_key, email=""):
    plan = get_plan(plan_key)
    if not plan:
        return {"ok": False, "error": f"Unsupported plan: {plan_key}"}
    if not is_payment_available():
        return {"ok": False, "error": "License server is not configured."}
    return _request_json(
        "/api/licenses/create-checkout-session",
        {"email": str(email or "").strip(), "plan_key": plan.get("key")},
    )


def create_lifetime_session(email=""):
    return create_checkout_session("lifetime", email)


def confirm_session(session_id):
    return _request_json("/api/licenses/confirm-session", {"session_id": str(session_id or "").strip()})


def restore_account_remote(email, recovery_code):
    return _request_json(
        "/api/licenses/restore",
        {"email": str(email or "").strip(), "recovery_code": str(recovery_code or "").strip()},
    )


def create_portal_session(email, recovery_code):
    return _request_json(
        "/api/licenses/create-portal-session",
        {"email": str(email or "").strip(), "recovery_code": str(recovery_code or "").strip()},
    )


def open_checkout(session_result):
    url = str((session_result or {}).get("url", "")).strip()
    if not url:
        return False
    try:
        webbrowser.open(url)
        return True
    except Exception as exc:
        logging.error("Could not open browser checkout URL: %s", exc)
        return False

