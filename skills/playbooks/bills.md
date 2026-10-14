---
name: Bills
when: paying an electricity bill, or another bill (water, DTH, gas, broadband, FASTag), on the biller's own website
---
- pay_bill pays one bill on the biller's own website: it reads the bill, checks the name on it and that something is due, asks yes or no once with the amount, and the user pays by UPI (a QR, or a tap that opens their UPI app, approved with their UPI PIN) or by card (its details and the bank's one-time password in a hidden field). The first time, it asks for the account number itself, hidden, and keeps it.
- Which biller: the one the user names. When they don't ("pay my electricity bill"), use the one their saved accounts show (a saved bescom_account means BESCOM); with none, or several, ask which, offering the billers pay_bill lists.
- A biller pay_bill doesn't list: ask for its official website, and pass it as site only once the user confirms it is the biller's own. Never search for one, and never pay through Paytm, Amazon, PhonePe or another app's site.
- Amount: the amount due unless the user named one (a recharge, or part of the bill). Method: pass UPI or card when the user said which; else the tool asks with the methods the site shows.
- Never ask for an account number, card number, CVV, OTP or UPI PIN in chat.
- The tool's own approval is the only yes: never ask again whether to pay.
- Paid: say so in the report's words, with its reference. Nothing due, not approved, not paid, paid already: say so; nothing was paid. Unclear: tell them not to pay again until they've checked their bank's SMS or the biller's site.
