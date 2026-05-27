Display Control+ License Server Deployment
=========================================

Purpose
-------
The desktop app now expects a hosted licensing server for professional billing flows.
That server owns:
- checkout session creation;
- checkout confirmation;
- subscription lifecycle updates from Stripe webhooks;
- recovery-code account restore;
- billing portal sessions.

Environment
-----------
Use `.env.example` as the template.

Minimum required server variables:
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`
- `STRIPE_SUCCESS_URL_DISPLAY_CONTROL`
- `STRIPE_CANCEL_URL_DISPLAY_CONTROL`
- `STRIPE_BILLING_PORTAL_RETURN_URL_DISPLAY_CONTROL`

Recommended billing defaults:
- `DCP_TRIAL_DAYS=14`
- `DCP_PRICE_ANNUAL_CENTS=2499`
- `DCP_PRICE_LIFETIME_CENTS=4999`

Run locally
-----------
1. Create a `.env` file from `.env.example`.
2. Activate the repo virtual environment.
3. Start the server:

```powershell
venv\Scripts\python.exe license_server.py
```

4. Point the desktop app at it:

```powershell
$env:DISPLAY_CONTROL_LICENSE_SERVER = "http://127.0.0.1:8787"
venv\Scripts\python.exe main.py
```

Stripe test mode checklist
--------------------------
1. Use Stripe test keys, not live keys.
2. Configure the webhook endpoint to hit `/api/licenses/stripe-webhook`.
3. Validate these flows before any live release:
- annual checkout;
- lifetime checkout;
- restore on a second local profile or second machine;
- billing portal open;
- subscription cancellation webhook;
- trial expiry behavior.

Smoke validation
----------------
The automated suite covers the billing stack locally:

```powershell
venv\Scripts\python.exe -m unittest -v .\test_billing_system.py
```

Release readiness
-----------------
Before shipping a public installer:
1. Deploy the license server to a stable HTTPS endpoint.
2. Set `DISPLAY_CONTROL_LICENSE_SERVER` in the packaged app environment or build/deployment target.
3. Run one real Stripe test-mode annual checkout and one lifetime checkout from the packaged installer build.
4. Confirm recovery email delivery from the deployed SMTP account.
5. Confirm `Manage Billing` opens the Stripe billing portal for the same customer.