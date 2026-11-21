# Qualitify sample 2: a well-built shop API

The good twin of [Qualitify_sample_1](https://github.com/Dharmadeep-bot/Qualitify_sample_1): the same
small shop (login, orders, payments, refunds, exports, uploads, a checkout page), written the way it
should be. Qualitify should review it as **PASS** or close to it, with the AI reviewers finding little
or nothing. If it blocks something here, that is a false alarm worth looking at.

```
shop/config.py     settings from the environment; secrets have no defaults
shop/db.py         data layer: fixed SQL, values always bound parameters
shop/auth.py       bcrypt passwords, signed expiring sessions, secure random codes
shop/authz.py      sign-in required, admin only, "is this your order?"
shop/payments.py   amounts from our own records, provider tokens, HTTPS with a timeout
shop/reports.py    one query for many users, safe CSV, safe YAML, discounts parsed not evaluated
shop/uploads.py    allowed types, size limit, a name we choose, path kept in its folder
shop/app.py        routes, input checks, security headers, errors that reveal nothing
shop/templates/    pages with escaped values and an accessible checkout form
tests/             the security-relevant behaviour
```

## What it does right (the opposite of sample 1)

| Topic | Sample 1 | Sample 2 |
| --- | --- | --- |
| Secrets | hardcoded in the code | environment only; the app refuses to start without them |
| Passwords | MD5, plus a backdoor password | bcrypt; same time for unknown email and wrong password |
| Sessions | unsigned base64 anyone can edit | signed, expiring (JWT), role checked against the signature |
| Access | any user reads any order; admin routes open | the user comes from the session; an order is yours or a 404 |
| Money | refund/charge amount sent by the client | amount from our own order; refunds capped at what is left |
| SQL | strings joined into queries | fixed statements with bound parameters |
| Cards | stored, logged, kept in the browser | never touch this server; the provider gives a one-time token |
| Files | any name, any path, shell commands | allow-listed types, chosen names, no shell, folder checked |
| Parsing | `eval`, `pickle`, `yaml.load` | numbers parsed, `yaml.safe_load` |
| Web | XSS, template injection, debug mode | escaped templates, security headers, no debug |
| Dependencies | years-old pinned versions | current pinned versions |

## Run

```
pip install -r requirements.txt
set SHOP_SIGNING_KEY=<at least 32 random characters>
set SHOP_PAYMENT_URL=https://payments.example.com/charge
set SHOP_PAYMENT_CREDENTIAL=<from your provider>
python -m shop.app
python -m pytest
```
