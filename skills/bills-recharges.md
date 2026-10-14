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
3. For a recharge or an amount the goal names, type that amount; else pay the full amount due, never a minimum or partial amount the site pre-selects.
4. Press the pay button: the system asks the user's yes itself. Then follow the payment pages to the method the user picked.
5. UPI: choose the QR to scan; the system sends it to the user. Card: type [ask] into each card box.
6. After paying, read the page's confirmation and its reference, then end with done.
## Facts
- name: the name on the bill. amount_due: the bill's full amount due, never a minimum or partial amount. due_date.
- payable: the total on the payment page, with any fee. payee: who the payment page pays. reference: the payment's
  reference or transaction number after paying.
## Rules
- The account number is only ever the goal's [profile:…] placeholder: never another number, never asked in chat.
- Read the bill first: name, amount_due and due_date in facts before you press any pay button.
- Pay the full amount due: when the page offers a minimum, partial or other amount (often pre-selected), choose the full amount, unless the goal names an exact amount: then that one.
- How to pay: the goal may say "by UPI" or "by card". Else end with ask, and list as options only the methods the page shows (UPI, card).
- UPI: choose the option that shows a QR code to scan. The system sends the QR to the user, who pays with their UPI PIN.
- Card: choose credit or debit card, type [ask] into each card box (the name on the card too), and leave any "save card" box unticked.
- End with done when the page confirms the payment (with its reference), or says nothing is due.
- A bill that shows an error about the account ends with stop quoting the page.
- Never save a card, take an offer, sign up, or change the user's details.
