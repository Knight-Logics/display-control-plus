import base64
import getpass
import hashlib
import hmac
import json
import os
import platform
import secrets
import sys
import threading
import time
import re

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_PROFILE = os.environ.get(
    "DISPLAY_CONTROL_RUNTIME_PROFILE",
    "DisplayControlPlus" if getattr(sys, "frozen", False) else "DisplayControlPlus-DevLocal",
)
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", RUNTIME_PROFILE)
os.makedirs(RUNTIME_DIR, exist_ok=True)

LICENSE_PATH = os.path.join(RUNTIME_DIR, "license.json")
FREE_HOURS = 100
FREE_SECONDS = FREE_HOURS * 3600
RECOVERY_SIGNING_SECRET_BAKED = "DisplayControlPlus-Recovery-LocalFallback-CHANGE-ME"

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
    key = hashlib.sha256(("KnightLogics.DisplayControlPlus.v1|" + _machine_fingerprint()).encode("utf-8")).digest()
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, normalized, hashlib.sha256).hexdigest()


def _b64u_encode(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(text):
    text = str(text or "")
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _recovery_signing_secret():
    # Keep recovery signing stable for packaged installs while still allowing
    # controlled environment overrides for managed deployments.
    secret = (
        os.environ.get("RECOVERY_SIGNING_SECRET", "").strip()
        or os.environ.get("STRIPE_SECRET_KEY", "").strip()
        or RECOVERY_SIGNING_SECRET_BAKED
    )
    return str(secret).encode("utf-8")


def _normalize_recovery_code(recovery_code):
    """Normalize pasted recovery codes from email clients.

    Some clients insert hidden whitespace/newlines when wrapping very long
    tokens. Remove those artifacts before parsing.
    """
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


def _new_state():
    now = int(time.time())
    base = {
        "version": 1,
        "remaining_seconds": int(FREE_SECONDS),
        "lifetime": False,
        "used_seconds": 0,
        "customer_email": "",
        "recovery_code": "",
        "purchase_history": [],
        "restored_codes": [],
        "created_at": now,
        "updated_at": now,
    }
    base["signature"] = _sign_payload({k: v for k, v in base.items() if k != "signature"})
    return base


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
    state = dict(state)
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
            return state

        tampered_path = LICENSE_PATH + ".tampered"
        try:
            if os.path.exists(tampered_path):
                os.remove(tampered_path)
            os.replace(LICENSE_PATH, tampered_path)
        except Exception:
            pass

        # If tampering is detected, preserve app stability but revoke remaining metered time.
        reset = _new_state()
        reset["remaining_seconds"] = 0
        return _write_state(reset)


def get_status():
    state = load_or_init_state()
    remaining = max(0, int(state.get("remaining_seconds", 0)))
    lifetime = bool(state.get("lifetime", False))
    return {
        "customer_email": str(state.get("customer_email", "")).strip(),
        "recovery_code": str(state.get("recovery_code", "")).strip(),
        "remaining_seconds": remaining,
        "remaining_hours": round(remaining / 3600.0, 2),
        "remaining_display": format_seconds_display(remaining),
        "lifetime": lifetime,
        "can_protect": lifetime or remaining > 0,
    }


def format_seconds_display(total_seconds):
    try:
        total_seconds = max(0, int(total_seconds))
    except Exception:
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def consume_active_seconds(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return get_status()

    with _STATE_LOCK:
        state = load_or_init_state()
        if state.get("lifetime", False):
            return {
                "customer_email": str(state.get("customer_email", "")).strip(),
                "recovery_code": str(state.get("recovery_code", "")).strip(),
                "remaining_seconds": int(state.get("remaining_seconds", 0)),
                "remaining_hours": round(int(state.get("remaining_seconds", 0)) / 3600.0, 2),
                "remaining_display": format_seconds_display(int(state.get("remaining_seconds", 0))),
                "lifetime": True,
                "can_protect": True,
            }

        remaining = max(0, int(state.get("remaining_seconds", 0)) - seconds)
        used = max(0, int(state.get("used_seconds", 0)) + seconds)
        state["remaining_seconds"] = remaining
        state["used_seconds"] = used
        state = _write_state(state)

    return {
        "customer_email": str(state.get("customer_email", "")).strip(),
        "recovery_code": str(state.get("recovery_code", "")).strip(),
        "remaining_seconds": int(state.get("remaining_seconds", 0)),
        "remaining_hours": round(int(state.get("remaining_seconds", 0)) / 3600.0, 2),
        "remaining_display": format_seconds_display(int(state.get("remaining_seconds", 0))),
        "lifetime": bool(state.get("lifetime", False)),
        "can_protect": bool(state.get("lifetime", False)) or int(state.get("remaining_seconds", 0)) > 0,
    }


def _record_purchase(state, purchase_type, email, hours_added=0):
    purchase_history = list(state.get("purchase_history", []))
    normalized_email = str(email or "").strip()
    recovery_code = _build_recovery_token(purchase_type, hours_added, normalized_email)
    if normalized_email:
        state["customer_email"] = normalized_email
    state["recovery_code"] = recovery_code
    purchase_history.append({
        "type": purchase_type,
        "hours_added": float(hours_added),
        "email": normalized_email,
        "recovery_code": recovery_code,
        "ts": int(time.time()),
    })
    state["purchase_history"] = purchase_history[-25:]
    return recovery_code


def add_hours(hours, email=""):
    try:
        seconds = int(float(hours) * 3600)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return get_status()

    with _STATE_LOCK:
        state = load_or_init_state()
        recovery_code = str(state.get("recovery_code", "")).strip()
        if not state.get("lifetime", False):
            state["remaining_seconds"] = max(0, int(state.get("remaining_seconds", 0))) + seconds
            recovery_code = _record_purchase(state, "hours", email, hours)
            _write_state(state)
    status = get_status()
    status["recovery_code"] = recovery_code
    return status


def activate_lifetime(email=""):
    with _STATE_LOCK:
        state = load_or_init_state()
        state["lifetime"] = True
        recovery_code = _record_purchase(state, "lifetime", email, 0)
        _write_state(state)
    status = get_status()
    status["recovery_code"] = recovery_code
    return status


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
            state["remaining_seconds"] = max(0, int(state.get("remaining_seconds", 0))) + int(hours_added * 3600)

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

