---
name: payment
description: Choose how to pay at checkout
---
- The user always picks the payment method. A pre-selected or "preferred" saved card is not their pick: open the payment selector first, then ASK which method, listing only the methods it shows; never list one the page doesn't show.
- The method the user picked is already the selected one: leave it and go on to the final control; never reopen the selector to pick it again.
- Never ask for card details (card number, CVV, expiry) yourself: they come only after the user picks a card, and the system asks for them through a hidden field.
- UPI collect or app approval: TELL the amount and that the request is waiting in their UPI app, then WAIT; never ask for a UPI PIN.
- The final pay or place-order control is the commit: the system shows the user a summary from the page and asks once.
