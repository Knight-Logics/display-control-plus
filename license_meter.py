import base64
import getpass
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timezone

from billing_catalog import TRIAL_DAYS


APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_PROFILE = os.environ.get(
    "DISPLAY_CONTROL_RUNTIME_PROFILE",
    "DisplayControlPlus" if getattr(sys, "frozen", False) else "DisplayControlPlus-DevLocal",
)
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", RUNTIME_PROFILE)
os.makedirs(RUNTIME_DIR, exist_ok=True)

LICENSE_PATH = os.path.join(RUNTIME_DIR, "license.json")
RECOVERY_SIGNING_SECRET_BAKED = "DisplayControlPlus-Recovery-LocalFallback-CHANGE-ME"
ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due"}

_STATE_LOCK = threading.RLock()


def _machine_fingerprint():
    parts = [
        platform.node(),
        getpass.getuser(),
        os.environ.get("PROCESSOR_IDENTIFIER", ""),
        os.environ.get("COMPUTERNAME", ""),
    ]
    return "|".join(parts)


def _sign_payload(payload):
    key = hashlib.sha256(("KnightLogics.DisplayControlPlus.v2|" + _machine_fingerprint()).encode("utf-8")).digest()
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, normalized, hashlib.sha256).hexdigest()


def _b64u_encode(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(text):
    text = str(text or "")
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _recovery_signing_secret():
    secret = os.environ.get("RECOVERY_SIGNING_SECRET", "").strip() or RECOVERY_SIGNING_SECRET_BAKED
    return str(secret).encode("utf-8")


def _normalize_recovery_code(recovery_code):
    code = str(recovery_code or "")
    code = code.replace("\ufeff", "").replace("\u200b", "")
    code = re.sub(r"\s+", "", code)
    return code.strip().strip("`\"'")


def _build_recovery_token(purchase_type, hours_added, email):
    payload = {
        "v": 1,
        "app": "dcp",
        "type": str(purchase_type),
        "hours": float(hours_added or 0),
        "email": str(email or "").strip().lower(),
        "ts": int(time.time()),
        "nonce": secrets.token_hex(4),
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64u_encode(payload_json)
    sig = hmac.new(_recovery_signing_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"DCP2.{payload_b64}.{sig}"


def _parse_recovery_token(recovery_code):
    code = _normalize_recovery_code(recovery_code)
    if not code or not code.upper().startswith("DCP2."):
        return None
    try:
        prefix, payload_b64, sig = code.split(".", 2)
    except ValueError:
        return None

    if prefix.upper() != "DCP2":
        return None

    expected_sig = hmac.new(_recovery_signing_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(sig).lower(), expected_sig.lower()):
        return None

    try:
        payload = json.loads(_b64u_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict) or payload.get("app") != "dcp":
        return None
    return payload


def _iso_to_epoch(value):
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def _format_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return text


def _new_state():
    now = int(time.time())
    base = {
        "version": 2,
        "legacy_remaining_seconds": 0,
        "legacy_used_seconds": 0,
        "lifetime": False,
        "customer_email": "",
        "recovery_code": "",
        "customer_id": "",
        "subscription_id": "",
        "subscription_status": "inactive",
        "subscription_current_period_end": "",
        "subscription_cancel_at_period_end": False,
        "trial_started_at": now,
        "trial_ends_at": now + (TRIAL_DAYS * 86400),
        "purchase_history": [],
        "restored_codes": [],
        "created_at": now,
        "updated_at": now,
        "last_synced_at": 0,
    }
    base["signature"] = _sign_payload({k: v for k, v in base.items() if k != "signature"})
    return base


def _migrate_state(state):
    if int(state.get("version", 1) or 1) >= 2:
        migrated = dict(state)
        now = int(time.time())
        migrated.setdefault("legacy_remaining_seconds", max(0, int(migrated.get("legacy_remaining_seconds", 0) or 0)))
        migrated.setdefault("legacy_used_seconds", max(0, int(migrated.get("legacy_used_seconds", 0) or 0)))
        migrated.setdefault("customer_email", "")
        migrated.setdefault("recovery_code", "")
        migrated.setdefault("customer_id", "")
        migrated.setdefault("subscription_id", "")
        migrated.setdefault("subscription_status", "inactive")
        migrated.setdefault("subscription_current_period_end", "")
        migrated.setdefault("subscription_cancel_at_period_end", False)
        migrated.setdefault("trial_started_at", int(migrated.get("created_at", now) or now))
        migrated.setdefault("trial_ends_at", int(migrated.get("trial_started_at", now) or now) + (TRIAL_DAYS * 86400))
        migrated.setdefault("purchase_history", [])
        migrated.setdefault("restored_codes", [])
        migrated.setdefault("created_at", now)
        migrated.setdefault("updated_at", now)
        migrated.setdefault("last_synced_at", 0)
        migrated["version"] = 2
        return migrated

    now = int(time.time())
    created_at = int(state.get("created_at", now) or now)
    migrated = _new_state()
    migrated.update(
        {
            "legacy_remaining_seconds": max(0, int(state.get("remaining_seconds", 0) or 0)),
            "legacy_used_seconds": max(0, int(state.get("used_seconds", 0) or 0)),
            "lifetime": bool(state.get("lifetime", False)),
            "customer_email": str(state.get("customer_email", "")).strip(),
            "recovery_code": str(state.get("recovery_code", "")).strip(),
            "purchase_history": list(state.get("purchase_history", [])),
            "restored_codes": list(state.get("restored_codes", [])),
            "created_at": created_at,
            "updated_at": int(state.get("updated_at", created_at) or created_at),
            "trial_started_at": created_at,
            "trial_ends_at": created_at + (TRIAL_DAYS * 86400),
        }
    )
    return migrated


def _verify_state(state):
    if not isinstance(state, dict):
        return False
    signature = state.get("signature")
    payload = {k: v for k, v in state.items() if k != "signature"}
    if not isinstance(signature, str):
        return False
    expected = _sign_payload(payload)
    return hmac.compare_digest(signature, expected)


def _write_state(state):
    state = dict(_migrate_state(state))
    state["updated_at"] = int(time.time())
    payload = {k: v for k, v in state.items() if k != "signature"}
    state["signature"] = _sign_payload(payload)
    with open(LICENSE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return state


def load_or_init_state():
    with _STATE_LOCK:
        if not os.path.exists(LICENSE_PATH):
            return _write_state(_new_state())
        try:
            with open(LICENSE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            return _write_state(_new_state())

        if _verify_state(state):
            migrated = _migrate_state(state)
            if migrated != state:
                return _write_state(migrated)
            return state

        tampered_path = LICENSE_PATH + ".tampered"
        try:
            if os.path.exists(tampered_path):
                os.remove(tampered_path)
            os.replace(LICENSE_PATH, tampered_path)
        except Exception:
            pass

        reset = _new_state()
        reset["trial_started_at"] = int(time.time())
        reset["trial_ends_at"] = reset["trial_started_at"]
        return _write_state(reset)


def format_seconds_display(total_seconds):
    try:
        total_seconds = max(0, int(total_seconds))
    except Exception:
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _subscription_is_active(state, now=None):
    now = int(time.time() if now is None else now)
    status = str(state.get("subscription_status", "inactive")).strip().lower()
    current_period_end = _iso_to_epoch(state.get("subscription_current_period_end", ""))
    if status not in ACTIVE_SUBSCRIPTION_STATUSES:
        return False
    if current_period_end and current_period_end < now:
        return False
    return bool(state.get("subscription_id")) or status in ACTIVE_SUBSCRIPTION_STATUSES


def _trial_is_active(state, now=None):
    now = int(time.time() if now is None else now)
    if max(0, int(state.get("legacy_remaining_seconds", 0) or 0)) > 0:
        return False
    if bool(state.get("lifetime", False)) or _subscription_is_active(state, now):
        return False
    trial_end = int(state.get("trial_ends_at", 0) or 0)
    return trial_end > now


def _current_billing_kind(state, now=None):
    now = int(time.time() if now is None else now)
    if bool(state.get("lifetime", False)):
        return "lifetime"
    if _subscription_is_active(state, now):
        return "subscription"
    if max(0, int(state.get("legacy_remaining_seconds", 0) or 0)) > 0:
        return "legacy_credits"
    if _trial_is_active(state, now):
        return "trial"
    return "none"


def _record_purchase(state, purchase_type, email, hours_added=0, plan_key="", recovery_code=""):
    purchase_history = list(state.get("purchase_history", []))
    normalized_email = str(email or "").strip().lower()
    resolved_code = _normalize_recovery_code(recovery_code)
    if not resolved_code:
        resolved_code = _build_recovery_token(purchase_type, hours_added, normalized_email)
    if normalized_email:
        state["customer_email"] = normalized_email
    state["recovery_code"] = resolved_code
    purchase_history.append(
        {
            "type": purchase_type,
            "plan_key": plan_key,
            "hours_added": float(hours_added),
            "email": normalized_email,
            "recovery_code": resolved_code,
            "ts": int(time.time()),
        }
    )
    state["purchase_history"] = purchase_history[-50:]
    return resolved_code


def get_status():
    state = load_or_init_state()
    now = int(time.time())
    billing_kind = _current_billing_kind(state, now)
    legacy_remaining = max(0, int(state.get("legacy_remaining_seconds", 0) or 0))
    trial_remaining = max(0, int(state.get("trial_ends_at", 0) or 0) - now) if _trial_is_active(state, now) else 0
    limited_remaining = legacy_remaining if billing_kind == "legacy_credits" else trial_remaining if billing_kind == "trial" else 0
    subscription_status = str(state.get("subscription_status", "inactive")).strip() or "inactive"

    if billing_kind == "lifetime":
        plan_key = "lifetime"
        plan_label = "Lifetime License"
    elif billing_kind == "subscription":
        plan_key = "annual"
        plan_label = "Annual Subscription"
    elif billing_kind == "legacy_credits":
        plan_key = "legacy_credits"
        plan_label = "Legacy Credits"
    elif billing_kind == "trial":
        plan_key = "trial"
        plan_label = "Free Trial"
    else:
        plan_key = ""
        plan_label = ""

    return {
        "customer_email": str(state.get("customer_email", "")).strip(),
        "recovery_code": str(state.get("recovery_code", "")).strip(),
        "remaining_seconds": limited_remaining,
        "remaining_hours": round(limited_remaining / 3600.0, 2),
        "remaining_display": format_seconds_display(limited_remaining),
        "legacy_remaining_seconds": legacy_remaining,
        "legacy_remaining_hours": round(legacy_remaining / 3600.0, 2),
        "legacy_remaining_display": format_seconds_display(legacy_remaining),
        "lifetime": bool(state.get("lifetime", False)),
        "billing_kind": billing_kind,
        "plan_key": plan_key,
        "plan_label": plan_label,
        "trial_active": billing_kind == "trial",
        "trial_days_remaining": max(0, int((trial_remaining + 86399) // 86400)) if trial_remaining else 0,
        "trial_ends_at": int(state.get("trial_ends_at", 0) or 0),
        "trial_display": format_seconds_display(trial_remaining),
        "subscription_active": billing_kind == "subscription",
        "subscription_status": subscription_status,
        "subscription_current_period_end": str(state.get("subscription_current_period_end", "")).strip(),
        "subscription_current_period_end_display": _format_date(state.get("subscription_current_period_end", "")),
        "subscription_cancel_at_period_end": bool(state.get("subscription_cancel_at_period_end", False)),
        "can_protect": billing_kind in {"trial", "subscription", "lifetime", "legacy_credits"},
    }


def consume_active_seconds(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return get_status()

    with _STATE_LOCK:
        state = load_or_init_state()
        if _current_billing_kind(state) == "legacy_credits":
            remaining = max(0, int(state.get("legacy_remaining_seconds", 0) or 0) - seconds)
            used = max(0, int(state.get("legacy_used_seconds", 0) or 0) + seconds)
            state["legacy_remaining_seconds"] = remaining
            state["legacy_used_seconds"] = used
            _write_state(state)
    return get_status()


def add_hours(hours, email=""):
    try:
        seconds = int(float(hours) * 3600)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return get_status()

    with _STATE_LOCK:
        state = load_or_init_state()
        if not state.get("lifetime", False):
            state["legacy_remaining_seconds"] = max(0, int(state.get("legacy_remaining_seconds", 0) or 0)) + seconds
            _record_purchase(state, "hours", email, hours_added=hours, plan_key="legacy_credits")
            _write_state(state)
    return get_status()


def activate_lifetime(email="", recovery_code=""):
    with _STATE_LOCK:
        state = load_or_init_state()
        state["lifetime"] = True
        _record_purchase(state, "lifetime", email, plan_key="lifetime", recovery_code=recovery_code)
        _write_state(state)
    return get_status()


def activate_subscription(
    email="",
    plan_key="annual",
    current_period_end="",
    recovery_code="",
    status="active",
    subscription_id="",
    customer_id="",
    cancel_at_period_end=False,
):
    with _STATE_LOCK:
        state = load_or_init_state()
        if email:
            state["customer_email"] = str(email).strip().lower()
        state["subscription_status"] = str(status or "active").strip() or "active"
        state["subscription_current_period_end"] = str(current_period_end or "").strip()
        state["subscription_id"] = str(subscription_id or "").strip()
        state["customer_id"] = str(customer_id or "").strip()
        state["subscription_cancel_at_period_end"] = bool(cancel_at_period_end)
        _record_purchase(state, "subscription", email, plan_key=plan_key, recovery_code=recovery_code)
        _write_state(state)
    return get_status()


def sync_remote_account(account_payload, recovery_code=""):
    if not isinstance(account_payload, dict):
        return get_status()

    with _STATE_LOCK:
        state = load_or_init_state()
        email = str(account_payload.get("email", state.get("customer_email", ""))).strip().lower()
        if email:
            state["customer_email"] = email
        resolved_code = _normalize_recovery_code(account_payload.get("recovery_code", "") or recovery_code or state.get("recovery_code", ""))
        if resolved_code:
            state["recovery_code"] = resolved_code
        state["customer_id"] = str(account_payload.get("customer_id", state.get("customer_id", ""))).strip()
        state["subscription_id"] = str(account_payload.get("subscription_id", state.get("subscription_id", ""))).strip()
        state["subscription_status"] = str(account_payload.get("subscription_status", state.get("subscription_status", "inactive"))).strip() or "inactive"
        state["subscription_current_period_end"] = str(account_payload.get("subscription_current_period_end", state.get("subscription_current_period_end", ""))).strip()
        state["subscription_cancel_at_period_end"] = bool(account_payload.get("subscription_cancel_at_period_end", state.get("subscription_cancel_at_period_end", False)))
        if bool(account_payload.get("lifetime", False)):
            state["lifetime"] = True
        state["last_synced_at"] = int(time.time())
        _write_state(state)
    return get_status()


def restore_from_recovery_code(recovery_code, email=""):
    recovery_code = _normalize_recovery_code(recovery_code)
    email = str(email or "").strip().lower()
    if not recovery_code:
        return {"ok": False, "error": "Recovery code is required."}
    if not email:
        return {"ok": False, "error": "Purchase email is required to restore this code."}

    with _STATE_LOCK:
        state = load_or_init_state()
        purchase_history = list(state.get("purchase_history", []))
        restored_codes = {
            _normalize_recovery_code(code)
            for code in state.get("restored_codes", [])
            if _normalize_recovery_code(code)
        }

        match = None
        for record in purchase_history:
            record_code = str(record.get("recovery_code", "")).strip()
            record_email = str(record.get("email", "")).strip().lower()
            if record_code != recovery_code:
                continue
            if record_email and record_email != email:
                continue
            match = record
            break

        token_payload = None
        if match is None:
            token_payload = _parse_recovery_token(recovery_code)
            if token_payload is None:
                return {"ok": False, "error": "Recovery code not found or invalid."}

            token_email = str(token_payload.get("email", "")).strip().lower()
            if token_email and token_email != email:
                return {"ok": False, "error": "Recovery code and email do not match."}
            if not token_email:
                return {"ok": False, "error": "This recovery code does not contain a purchase email and cannot be restored automatically."}

            match = {
                "type": token_payload.get("type", "hours"),
                "hours_added": float(token_payload.get("hours", 0) or 0),
                "email": token_email,
            }

        if recovery_code in restored_codes:
            status = get_status()
            return {
                "ok": True,
                "already_restored": True,
                "message": "This code was already redeemed on this installation.",
                "status": status,
            }

        purchase_type = str(match.get("type", "")).strip().lower()
        hours_added = float(match.get("hours_added", 0) or 0)

        if purchase_type == "lifetime":
            state["lifetime"] = True
        elif hours_added > 0:
            state["legacy_remaining_seconds"] = max(0, int(state.get("legacy_remaining_seconds", 0) or 0)) + int(hours_added * 3600)

        if email:
            state["customer_email"] = email

        restored_codes.add(recovery_code)
        state["restored_codes"] = sorted(restored_codes)
        _write_state(state)

    status = get_status()
    return {
        "ok": True,
        "already_restored": False,
        "message": "Account access restored.",
        "status": status,
    }

