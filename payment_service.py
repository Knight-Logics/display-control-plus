"""
Stripe payment service for Display Control+ licensing.
Adapted from AutoTop5_Showcase_App billing system.
"""

import os
import smtplib
import json
import time
import logging
import webbrowser
import urllib.error
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SUCCESS_URL = os.environ.get("STRIPE_SUCCESS_URL_DISPLAY_CONTROL", "http://127.0.0.1:8200/success")
STRIPE_CANCEL_URL = os.environ.get("STRIPE_CANCEL_URL_DISPLAY_CONTROL", "http://127.0.0.1:8200/cancel")
STRIPE_CURRENCY = os.environ.get("STRIPE_CURRENCY", "usd").lower()
LICENSE_SERVER_BASE_URL = os.environ.get("DISPLAY_CONTROL_LICENSE_SERVER", "").strip()
LICENSE_SERVER_TIMEOUT_SEC = max(1.0, float(os.environ.get("LICENSE_SERVER_TIMEOUT_SEC", "6")))

# Pricing: cents per unit
STRIPE_PRICE_500H_CENTS = 500  # $5.00
STRIPE_PRICE_1200H_CENTS = 1000  # $10.00
STRIPE_PRICE_3000H_CENTS = 2000  # $20.00
STRIPE_PRICE_LIFETIME_CENTS = 2999  # $29.99

PAYMENT_CONFIGURED = bool(STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY)

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "").replace(" ", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
APP_NAME = os.environ.get("APP_NAME", "Display Control+")

SMTP_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS and SMTP_FROM)
SMTP_RETRY_ATTEMPTS = max(1, int(os.environ.get("SMTP_RETRY_ATTEMPTS", "3")))
SMTP_RETRY_DELAY_SEC = max(0.5, float(os.environ.get("SMTP_RETRY_DELAY_SEC", "1.5")))

stripe = None
if PAYMENT_CONFIGURED:
    try:
        import stripe as _stripe

        stripe = _stripe
        stripe.api_key = STRIPE_SECRET_KEY
    except Exception:
        stripe = None


def is_payment_available():
    """Check if Stripe is configured and available for payments."""
    return bool(LICENSE_SERVER_BASE_URL) or (PAYMENT_CONFIGURED and stripe is not None)


def _post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=LICENSE_SERVER_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))


def create_checkout_session(hours, email=""):
    """
    Create a Stripe checkout session for hours purchase.
    
    Args:
        hours (int): Number of hours to purchase (500, 1200, or 3000).
        email (str): Customer email for receipt.
    
    Returns:
        dict: {"url": checkout_url, "session_id": session_id, "error": error_msg} or {"error": error_msg}
    """
    if not is_payment_available():
        return {"error": "Stripe is not configured. Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY env vars."}

    try:
        hours = int(hours)
    except (ValueError, TypeError):
        return {"error": "hours must be an integer"}

    if hours == 500:
        unit_amount = STRIPE_PRICE_500H_CENTS
        product_name = "Display Control+ 500 Hours"
        description = "500 hours of burn-in protection"
    elif hours == 1200:
        unit_amount = STRIPE_PRICE_1200H_CENTS
        product_name = "Display Control+ 1200 Hours"
        description = "1200 hours of burn-in protection"
    elif hours == 3000:
        unit_amount = STRIPE_PRICE_3000H_CENTS
        product_name = "Display Control+ 3000 Hours"
        description = "3000 hours of burn-in protection"
    else:
        return {"error": f"hours must be 500, 1200, or 3000, not {hours}"}

    plan_key = {
        500: "hours_500",
        1200: "hours_1200",
        3000: "hours_3000",
    }[hours]
    if LICENSE_SERVER_BASE_URL:
        try:
            return _post_json(f"{LICENSE_SERVER_BASE_URL}/api/licenses/create-checkout-session", {"email": email, "plan_key": plan_key})
        except Exception as e:
            logging.warning(f"License server unavailable ({e}), falling back to Stripe")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            success_url=STRIPE_SUCCESS_URL,
            cancel_url=STRIPE_CANCEL_URL,
            customer_email=email.strip() if email else None,
            line_items=[
                {
                    "price_data": {
                        "currency": STRIPE_CURRENCY,
                        "product_data": {
                            "name": product_name,
                            "description": description,
                        },
                        "unit_amount": unit_amount,
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "product": "display_control_plus",
                "hours": str(hours),
                "email": email.strip() if email else "",
            },
        )
        return {
            "url": session.url,
            "session_id": session.id,
        }
    except Exception as e:
        return {"error": f"Stripe error: {str(e)}"}


def create_lifetime_session(email=""):
    """
    Create a Stripe checkout session for lifetime license.
    
    Args:
        email (str): Customer email for receipt.
    
    Returns:
        dict: {"url": checkout_url, "session_id": session_id} or {"error": error_msg}
    """
    if not is_payment_available():
        return {"error": "Stripe is not configured. Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY env vars."}

    if LICENSE_SERVER_BASE_URL:
        try:
            return _post_json(f"{LICENSE_SERVER_BASE_URL}/api/licenses/create-checkout-session", {"email": email, "plan_key": "lifetime"})
        except Exception as e:
            logging.warning(f"License server unavailable ({e}), falling back to Stripe")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            success_url=STRIPE_SUCCESS_URL,
            cancel_url=STRIPE_CANCEL_URL,
            customer_email=email.strip() if email else None,
            line_items=[
                {
                    "price_data": {
                        "currency": STRIPE_CURRENCY,
                        "product_data": {
                            "name": "Display Control+ Lifetime License",
                            "description": "Lifetime burn-in protection - unlimited hours",
                        },
                        "unit_amount": STRIPE_PRICE_LIFETIME_CENTS,
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "product": "display_control_plus",
                "type": "lifetime",
                "email": email.strip() if email else "",
            },
        )
        return {
            "url": session.url,
            "session_id": session.id,
        }
    except Exception as e:
        return {"error": f"Stripe error: {str(e)}"}


def confirm_session(session_id):
    """
    Confirm a Stripe checkout session after successful payment.
    
    Args:
        session_id (str): The Stripe session ID to confirm.
    
    Returns:
        dict: {"paid": True, "metadata": {...}} or {"error": error_msg}
    """
    if not is_payment_available():
        return {"error": "Stripe is not configured."}

    if LICENSE_SERVER_BASE_URL:
        try:
            return _post_json(f"{LICENSE_SERVER_BASE_URL}/api/licenses/confirm-session", {"session_id": session_id})
        except Exception as e:
            logging.warning(f"License server unavailable during confirm ({e}), falling back to Stripe")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        return {"error": f"Could not retrieve session: {str(e)}"}

    if session.get("payment_status") != "paid":
        return {"error": "Payment not yet completed.", "status": session.get("payment_status")}

    metadata = session.get("metadata", {})
    return {
        "paid": True,
        "session_id": session_id,
        "metadata": metadata,
        "customer_email": session.get("customer_email", ""),
    }


def open_checkout(session_result):
    """
    Open the Stripe checkout URL in the default browser.
    
    Args:
        session_result (dict): Result from create_checkout_session or create_lifetime_session.
    
    Returns:
        bool: True if URL was opened, False if error.
    """
    if "error" in session_result:
        return False
    
    url = session_result.get("url")
    if url:
        try:
            webbrowser.open(url)
            return True
        except Exception as e:
            print(f"Could not open browser: {e}")
            return False
    return False


def send_recovery_email(to_email, recovery_code, plan_label):
    """Send purchase recovery email with account code."""
    to_email = str(to_email or "").strip()
    recovery_code = str(recovery_code or "").strip()
    plan_label = str(plan_label or "Purchased Plan").strip()

    if not to_email:
        return {"ok": False, "error": "Email is required."}
    if not recovery_code:
        return {"ok": False, "error": "Recovery code is required."}
    if not SMTP_CONFIGURED:
        return {"ok": False, "error": "SMTP is not configured."}

    subject = f"{APP_NAME} Purchase Confirmation & Recovery Code"
    body = (
        f"Thank you for your purchase of {plan_label}.\n\n"
        f"Your recovery code:\n{recovery_code}\n\n"
        f"Use this code in the app via 'Restore Account' if you reinstall or reset your setup.\n\n"
        f"Keep this email safe for account recovery."
    )

    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    last_error = "unknown"
    for attempt in range(1, SMTP_RETRY_ATTEMPTS + 1):
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.ehlo()
                if server.has_extn("starttls"):
                    server.starttls()
                    server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_FROM, [to_email], msg.as_string())
            return {"ok": True}
        except Exception as exc:
            last_error = str(exc)
            if attempt < SMTP_RETRY_ATTEMPTS:
                time.sleep(SMTP_RETRY_DELAY_SEC)

    return {"ok": False, "error": f"SMTP send failed after {SMTP_RETRY_ATTEMPTS} attempt(s): {last_error}"}


def restore_account_remote(email, recovery_code):
    if not LICENSE_SERVER_BASE_URL:
        return {"ok": False, "error": "License server is not configured."}
    try:
        return _post_json(f"{LICENSE_SERVER_BASE_URL}/api/licenses/restore", {"email": email, "recovery_code": recovery_code})
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            return payload
        except Exception:
            return {"ok": False, "error": f"HTTP error: {exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": f"License server error: {exc}"}

