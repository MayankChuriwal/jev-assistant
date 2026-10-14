---
name: bills-recharges
description: Pay a bill or recharge (electricity, water, DTH, gas, broadband, FASTag) on the biller's own website
inputs: provider, amount?
sites:
uses: payment
summary: biller, account, amount due, due date, payment method, total
report: transaction id, amount, biller
---
## Plan
1. On the biller's own pay page, type the account placeholder the goal gives into its account, consumer or customer number box, and get the bill.
2. Read the bill: the name on it, the amount due, the due date. Put them in facts before any pay button.
3. For a recharge or an amount the goal names, type that amount; else leave the amount the site fills in.
4. Press the pay button: the system asks the user's yes itself. Then follow the payment pages to the method the user picked.
5. UPI: choose the QR to scan; the system sends it to the user. Card: type [ask] into each card box.
6. After paying, read the page's confirmation and its reference, then end with done.
## Rules
- The account number is only ever the goal's [profile:…] placeholder: never another number, never asked in chat.
- A bill that shows nothing due, or an error about the account, ends with done or stop quoting the page.
- Never save a card, take an offer, sign up, or change the user's details.
