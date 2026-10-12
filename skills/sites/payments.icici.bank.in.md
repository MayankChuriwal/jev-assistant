---
host: payments.icici.bank.in
name: ICICI Bank net banking sign-in
aliases: ICICI net banking, ICICI Bank login
start: https://payments.icici.bank.in/sm/login-user-id
learn: no
---
- ICICI Bank's net banking sign-in, opened by iShop's "Proceed to Login". Type [profile:icici_user_id] into "USER ID"; the password goes into "PASSWORD" through the hidden field (the system asks the user for it). Leave "Use Virtual Keyboard" unticked and press SECURE LOGIN.
- A code sent by SMS comes through the hidden field. ICICI then brings the user back to iShop.
- Never press "Forgot your User ID?" or "Forgot your password?". If the page says the User ID or password is wrong, STOP and say so: a second wrong try may lock the account.
