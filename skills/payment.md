---
name: payment
description: Choose how to pay at checkout
---
- The user always picks the payment method: ASK with every method the page shows (saved cards, UPI, wallets, net banking, pay later, cash or pay on delivery), not only the pre-selected one; open the payment selector to see them all.
- Card details (CVV, card number) come only after the user picks a card; the system asks for them through a hidden field.
- UPI collect or app approval: TELL the amount and that the request is waiting in their UPI app, then WAIT; never ask for a UPI PIN.
- The final pay or place-order control is the commit: the system shows the user a summary from the page and asks once.
