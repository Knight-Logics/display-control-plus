import base64
import getpass
import hashlib
import hmac
import json
import logging
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
# Keep a previously synced subscription usable when the license server cannot
# confirm a renewal. 14 days matches the local trial window.
OFFLINE_GRACE_SECONDS = 14 * 24 * 60 * 60
SIGN_PREFIX_V2 = "KnightLogics.DisplayControlPlus.v2|"
SIGN_PREFIX_V1 = "KnightLogics.DisplayControlPlus.v1|"

_STATE_LOCK = threading.RLock()
_LOG = logging.getLogger("license_meter")


def _fingerprint_parts():
    node = str(platform.node() or "")
    user = str(getpass.getuser() or "")
    processor = str(os.environ.get("PROCESSOR_IDENTIFIER", "") or "")
    computer = str(os.environ.get("COMPUTERNAME", "") or node)
    return node, user, processor, computer


def _stable_fingerprint():
    # PROCESSOR_IDENTIFIER is not present in every process (scheduled tasks,
    # elevated vs unelevated, service-hosted launches). Signing with it made
    # legitimate license files look tampered.
    node, user, _processor, computer = _fingerprint_parts()
    return "|".join((node, user, "", computer))


def _fingerprint_variants():
    node, user, processor, computer = _fingerprint_parts()
    raw = [
        (node, user, "", computer),
        (node, user, processor, computer),
        (node, user.lower(), "", computer),
        (node, user.lower(), processor, computer),
        (computer, user, "", computer),
        (computer, user, processor, computer),
        (node, user, processor, node),
        (node.lower(), user.lower(), "", computer.lower()),
        (node.lower(), user.lower(), processor, computer.lower()),
    ]
    variants = []
    seen = set()
    for parts in raw:
        fingerprint = "|".join(parts)
        if fingerprint not in seen:
            seen.add(fingerprint)
            variants.append(fingerprint)
    return variants


def _sign_payload(payload, prefix=None, fingerprint=None):
    prefix = SIGN_PREFIX_V2 if prefix is None else prefix
    fingerprint = _stable_fingerprint() if fingerprint is None else fingerprint
    key = hashlib.sha256((prefix + fingerprint).encode("utf-8")).digest()
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
    # v1 only sold metered hours. Backdating the commercial trial to created_at
    # expired it immediately for existing installs and blocked protection.
    migrated.update(
        {
            "legacy_remaining_seconds": max(0, int(state.get("remaining_seconds", state.get("legacy_remaining_seconds", 0)) or 0)),
            "legacy_used_seconds": max(0, int(state.get("used_seconds", state.get("legacy_used_seconds", 0)) or 0)),
            "lifetime": bool(state.get("lifetime", False)),
            "customer_email": str(state.get("customer_email", "")).strip(),
            "recovery_code": str(state.get("recovery_code", "")).strip(),
            "purchase_history": list(state.get("purchase_history", [])),
            "restored_codes": list(state.get("restored_codes", [])),
            "created_at": created_at,
            "updated_at": int(state.get("updated_at", created_at) or created_at),
            "customer_id": str(state.get("customer_id", "")).strip(),
            "subscription_id": str(state.get("subscription_id", "")).strip(),
            "subscription_status": str(state.get("subscription_status", "inactive")).strip() or "inactive",
            "subscription_current_period_end": str(state.get("subscription_current_period_end", "")).strip(),
            "subscription_cancel_at_period_end": bool(state.get("subscription_cancel_at_period_end", False)),
            "last_synced_at": int(state.get("last_synced_at", 0) or 0),
            "trial_started_at": now,
            "trial_ends_at": now + (TRIAL_DAYS * 86400),
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
    for prefix in (SIGN_PREFIX_V2, SIGN_PREFIX_V1):
        for fingerprint in _fingerprint_variants():
            expected = _sign_payload(payload, prefix=prefix, fingerprint=fingerprint)
            if hmac.compare_digest(signature, expected):
                return True
    return False


def _signature_is_present(state):
    signature = state.get("signature") if isinstance(state, dict) else None
    return isinstance(signature, str) and len(signature) == 64 and all(ch in "0123456789abcdef" for ch in signature.lower())


def _drop_stale_tamper_marker():
    path = LICENSE_PATH + ".tampered"
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
        _LOG.info("Removed stale license.json.tampered left by a signature mismatch.")
    except OSError as exc:
        _LOG.warning("Could not remove stale license.json.tampered: %s", exc)


def _quarantine_license(suffix):
    dest = LICENSE_PATH + suffix
    try:
        if os.path.exists(dest):
            os.remove(dest)
        if os.path.exists(LICENSE_PATH):
            os.replace(LICENSE_PATH, dest)
    except OSError as exc:
        _LOG.warning("Could not quarantine license file as %s: %s", suffix, exc)


def _write_state(state):
    state = dict(_migrate_state(state))
    state["updated_at"] = int(time.time())
    payload = {k: v for k, v in state.items() if k != "signature"}
    state["signature"] = _sign_payload(payload)
    tmp_path = LICENSE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, LICENSE_PATH)
    return state


def load_or_init_state():
    with _STATE_LOCK:
        if not os.path.exists(LICENSE_PATH):
            return _write_state(_new_state())
        try:
            with open(LICENSE_PATH, "r", encoding="utf-8-sig") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            _LOG.warning("Could not read license file (%s). Starting a new trial.", exc)
            _quarantine_license(".unreadable")
            return _write_state(_new_state())

        if not isinstance(state, dict):
            _LOG.warning("License file was not an object. Starting a new trial.")
            _quarantine_license(".unreadable")
            return _write_state(_new_state())

        verified = _verify_state(state)
        if not verified and not _signature_is_present(state):
            # A missing signature is an actual local edit. v1 files and
            # fingerprint drift still carry a 64-char HMAC and are handled below.
            _LOG.warning("License file has no valid signature. Local entitlements were not kept.")
            _quarantine_license(".tampered")
            return _write_state(_new_state())

        if not verified:
            _LOG.warning(
                "License signature did not match this session. "
                "Keeping the stored trial or license and re-signing. "
                "This happens for v1.0 license files and when processor or logon "
                "environment details differ between the dashboard and the background process."
            )

        migrated = _migrate_state(state)
        if (not verified) or migrated != state:
            written = _write_state(migrated)
            _drop_stale_tamper_marker()
            return written
        _drop_stale_tamper_marker()
        return state


def format_seconds_display(total_seconds):
    try:
        total_seconds = max(0, int(total_seconds))
    except (TypeError, ValueError):
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _offline_grace_active(state, now=None):
    now = int(time.time() if now is None else now)
    status = str(state.get("subscription_status", "inactive")).strip().lower()
    if status not in ACTIVE_SUBSCRIPTION_STATUSES:
        return False
    if int(state.get("last_synced_at", 0) or 0) <= 0:
        return False
    current_period_end = _iso_to_epoch(state.get("subscription_current_period_end", ""))
    if not current_period_end or current_period_end >= now:
        return False
    return current_period_end + OFFLINE_GRACE_SECONDS >= now


def _subscription_is_active(state, now=None):
    now = int(time.time() if now is None else now)
    status = str(state.get("subscription_status", "inactive")).strip().lower()
    if status not in ACTIVE_SUBSCRIPTION_STATUSES:
        return False
    current_period_end = _iso_to_epoch(state.get("subscription_current_period_end", ""))
    if current_period_end and current_period_end < now and not _offline_grace_active(state, now):
        return False
    return True


def _trial_is_active(state, now=None):
    now = int(time.time() if now is None else now)
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
    if _trial_is_active(state, now):
        return "trial"
    if max(0, int(state.get("legacy_remaining_seconds", 0) or 0)) > 0:
        return "legacy_credits"
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

    offline_grace = _offline_grace_active(state, now)
    server_error = str(state.get("last_server_error", "")).strip()
    if billing_kind == "none":
        block_reason = "trial ended and no active license is stored on this PC"
        if server_error:
            block_reason = f"{block_reason} (license server: {server_error})"
    else:
        block_reason = ""

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
        "offline_grace": offline_grace,
        "block_reason": block_reason,
        "server_error": server_error,
        "last_synced_at": int(state.get("last_synced_at", 0) or 0),
    }


def note_license_server_failure(error):
    """Remember a failed account sync without clearing a local trial or license."""
    message = str(error or "license server unavailable").strip() or "license server unavailable"
    with _STATE_LOCK:
        state = load_or_init_state()
        state["last_server_error"] = message[:300]
        state["last_server_error_at"] = int(time.time())
        _write_state(state)
    return get_status()


def consume_active_seconds(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
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
    except (TypeError, ValueError):
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

