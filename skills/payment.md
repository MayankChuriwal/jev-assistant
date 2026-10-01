---
name: payment
description: Choose how to pay at checkout
---
- The user always picks the payment method. A pre-selected or "preferred" saved card is not their pick: ASK which method, listing every method the page shows (cash or pay on delivery, UPI, wallets, cards, net banking, pay later); open the payment selector to see them all.
- Never ask for card details (card number, CVV, expiry) yourself: they come only after the user picks a card, and the system asks for them through a hidden field.
- UPI collect or app approval: TELL the amount and that the request is waiting in their UPI app, then WAIT; never ask for a UPI PIN.
- The final pay or place-order control is the commit: the system shows the user a summary from the page and asks once.
