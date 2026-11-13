"""HTTP client for the hosted Display Control+ billing service."""

import json
import logging
import os
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone

def _load_env_file():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


from billing_catalog import get_plan

_load_env_file()


# knight-account-api is served on knightlogics.com. An empty env value disables it.
DEFAULT_LICENSE_SERVER = "https://knightlogics.com"
LICENSE_SERVER_TIMEOUT_SEC = max(1.0, float(os.environ.get("LICENSE_SERVER_TIMEOUT_SEC", "6")))


def license_server_base_url():
    raw = os.environ.get("DISPLAY_CONTROL_LICENSE_SERVER")
    if raw is None:
        return DEFAULT_LICENSE_SERVER
    return str(raw).strip().rstrip("/")


def is_payment_available():
    return bool(license_server_base_url())


def _request_json(path, payload=None, method="POST"):
    base_url = license_server_base_url()
    if not base_url:
        return {"ok": False, "fail_open": False, "error": "License server is not configured."}

    url = f"{base_url}{path}"
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=LICENSE_SERVER_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {"ok": True}
            if not isinstance(parsed, dict):
                return {"ok": False, "fail_open": False, "error": "License server returned a non-object response."}
            parsed.setdefault("ok", True)
            parsed["fail_open"] = False
            parsed["http_status"] = int(getattr(resp, "status", 200) or 200)
            return parsed
    except urllib.error.HTTPError as exc:
        parsed = {}
        try:
            raw = exc.read().decode("utf-8")
            loaded = json.loads(raw) if raw else {}
            if isinstance(loaded, dict):
                parsed = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            parsed = {}
        parsed["ok"] = False
        parsed["http_status"] = int(exc.code)
        parsed.setdefault("error", f"HTTP error: {exc.code}")
        # Only the license-store outage sets fail_open. A 503 that means
        # "Stripe is not configured" must not keep a dead subscription alive.
        parsed["fail_open"] = bool(exc.code == 503 and parsed.get("fail_open") is True)
        logging.warning("Billing request failed: %s %s", path, parsed.get("error", "unknown error"))
        return parsed
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logging.error("Billing request exception for %s: %s", path, exc)
        return {"ok": False, "fail_open": False, "error": f"License server error: {exc}"}


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


def fetch_knight_account_status(email):
    """POST /api/knight-account action=status for the displaycontrol product."""
    return _request_json(
        "/api/knight-account",
        {
            "action": "status",
            "product_id": "displaycontrol",
            "email": str(email or "").strip().lower(),
        },
    )


def _iso_from_unix(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.lstrip("-").isdigit():
        return text
    epoch = int(text)
    if epoch <= 0:
        return ""
    if epoch > 10**11:
        epoch = epoch / 1000.0
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_account_payload(payload):
    """Map /api/licenses/* or /api/knight-account status into the local account shape."""
    if not isinstance(payload, dict):
        return {}
    source = payload.get("account") if isinstance(payload.get("account"), dict) else payload
    period = str(source.get("subscription_current_period_end") or "").strip()
    if not period and source.get("period_end"):
        period = _iso_from_unix(source.get("period_end"))
    status = str(source.get("subscription_status") or "").strip()
    if not status:
        if source.get("lifetime"):
            status = "active"
        elif source.get("subscription_active"):
            status = "past_due" if source.get("in_grace") else "active"
        else:
            status = "inactive"
    if source.get("in_grace") and status.lower() not in {"active", "trialing", "past_due"}:
        status = "past_due"
    try:
        grace_until = int(source.get("grace_until") or payload.get("grace_until") or 0)
    except (TypeError, ValueError):
        grace_until = 0
    try:
        grace_seconds = int(source.get("offline_grace_seconds") or payload.get("offline_grace_seconds") or 0)
    except (TypeError, ValueError):
        grace_seconds = 0
    return {
        "email": str(source.get("email") or payload.get("email") or "").strip().lower(),
        "recovery_code": str(source.get("recovery_code") or "").strip(),
        "lifetime": bool(source.get("lifetime")),
        "subscription_status": status,
        "subscription_current_period_end": period,
        "subscription_cancel_at_period_end": bool(source.get("subscription_cancel_at_period_end", False)),
        "subscription_id": str(source.get("subscription_id") or "").strip(),
        "customer_id": str(source.get("customer_id") or source.get("stripe_customer_id") or "").strip(),
        "entitlement_token": str(source.get("entitlement_token") or payload.get("entitlement_token") or "").strip(),
        "in_grace": bool(source.get("in_grace") or payload.get("in_grace")),
        "grace_until": grace_until,
        "offline_grace_seconds": grace_seconds,
    }


def sync_account(email, recovery_code):
    """Refresh from /api/licenses/restore and /api/knight-account.

    A 503 with fail_open is returned as-is so the caller can keep the paid
    license for the grace window. Status is not used as a grant by itself:
    /api/knight-account status is not recovery-code authenticated.
    """
    email = str(email or "").strip()
    recovery_code = str(recovery_code or "").strip()
    restore = restore_account_remote(email, recovery_code)
    status = fetch_knight_account_status(email) if email else {"ok": False, "fail_open": False, "error": "Email is required."}
    if restore.get("fail_open") or status.get("fail_open"):
        chosen = restore if restore.get("fail_open") else status
        try:
            grace_seconds = int(chosen.get("offline_grace_seconds") or 0)
        except (TypeError, ValueError):
            grace_seconds = 0
        return {
            "ok": False,
            "fail_open": True,
            "http_status": int(chosen.get("http_status") or 503),
            "error": chosen.get("error") or "License store is temporarily unavailable.",
            "offline_grace_seconds": grace_seconds or (7 * 24 * 60 * 60),
        }
    if not restore.get("ok"):
        return restore
    account = normalize_account_payload(restore)
    if status.get("ok"):
        extra = normalize_account_payload(status)
        same_email = not extra.get("email") or not account.get("email") or extra["email"] == account["email"]
        if same_email:
            if extra.get("entitlement_token"):
                account["entitlement_token"] = extra["entitlement_token"]
            if extra.get("in_grace"):
                account["in_grace"] = True
                account["grace_until"] = extra.get("grace_until") or account.get("grace_until") or 0
            if extra.get("offline_grace_seconds"):
                account["offline_grace_seconds"] = extra["offline_grace_seconds"]
            if extra.get("subscription_current_period_end") and not account.get("subscription_current_period_end"):
                account["subscription_current_period_end"] = extra["subscription_current_period_end"]
    account["ok"] = True
    account["fail_open"] = False
    return account


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

