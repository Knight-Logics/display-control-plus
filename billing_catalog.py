import os


def _env_int(name, default, minimum):
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except Exception:
        value = int(default)
    return max(minimum, value)


TRIAL_DAYS = _env_int("DCP_TRIAL_DAYS", 14, 1)
ANNUAL_PRICE_CENTS = _env_int("DCP_PRICE_ANNUAL_CENTS", 2499, 100)
LIFETIME_PRICE_CENTS = _env_int("DCP_PRICE_LIFETIME_CENTS", 4999, 100)

PLAN_CATALOG = {
    "annual": {
        "key": "annual",
        "label": "Display Control+ Annual",
        "short_label": "Annual",
        "description": "Recurring yearly protection for Display Control+.",
        "checkout_mode": "subscription",
        "billing_kind": "subscription",
        "interval": "year",
        "price_cents": ANNUAL_PRICE_CENTS,
    },
    "lifetime": {
        "key": "lifetime",
        "label": "Display Control+ Lifetime License",
        "short_label": "Lifetime",
        "description": "One-time lifetime access to Display Control+.",
        "checkout_mode": "payment",
        "billing_kind": "lifetime",
        "interval": None,
        "price_cents": LIFETIME_PRICE_CENTS,
    },
}


def get_plan(plan_key):
    return PLAN_CATALOG.get(str(plan_key or "").strip().lower())


def format_price(cents):
    return f"${(int(cents) / 100):.2f}"


def plan_button_label(plan_key):
    plan = get_plan(plan_key)
    if not plan:
        return "Unavailable"
    if plan.get("checkout_mode") == "subscription":
        return f"{plan.get('short_label', plan_key.title())} ({format_price(plan.get('price_cents', 0))}/yr)"
    return f"{plan.get('short_label', plan_key.title())} ({format_price(plan.get('price_cents', 0))})"


def plan_status_label(plan_key):
    plan = get_plan(plan_key)
    if not plan:
        return ""
    if plan.get("checkout_mode") == "subscription":
        return f"{plan.get('short_label', plan_key.title())} subscription"
    return plan.get("label", plan_key.title())