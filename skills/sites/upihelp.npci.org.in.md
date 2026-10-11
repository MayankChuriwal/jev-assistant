---
host: upihelp.npci.org.in
name: NPCI UPI Help
aliases: NPCI, UPI Help, MyUPI, UPI AutoPay, autopay, UPI mandate, UPI mandates
start: https://www.upihelp.npci.org.in/
learn: no
sign-in: auto
cards: payee
list: Manage recurring payments
keep: tab
---
- NPCI's MyUPI shows the user's UPI activity across all their UPI apps: AutoPay mandates (and their cancel, pause, resume), the last 60 days of transactions, complaints, their UPI ID and number, payee checks, fraud advice and FAQs.
- The tab stays signed in between requests: when the page already shows MyUPI's sidebar (Ask AI Assistant, UPI Autopay, UPI Transactions, Stay Safe, My Complaints), don't sign in again.
- Sign-in, in this order: type the user's phone ([profile:phone]) into the mobile number box ("Eg. 9876543210"); the captcha beside "Security Verification" goes to the user as a picture, and the text they read goes into that box; tick the "I have read and agree" checkbox once (a second click unticks it), never opening its Terms, Privacy Policy or Disclaimer links; press Continue. The 6-digit code sent by SMS then goes into the one-time code boxes through the hidden field; press Proceed.
- A wrong captcha or an expired code shows an error: press Refresh CAPTCHA (or Resend code) and ask again; never guess.
- Mandates: UPI Autopay in the sidebar lists them as cards under a category heading, each with its payee, amount (the most it may take), Active on (UPI app), Linked bank, Frequency, Executed N times, Total paid, and Valid till, and Pause and Cancel. Their order changes from one load to the next: find a card by its payee, never by position. To answer what mandates the user has, name every card's payee with its amount and app.
- A card is the merchant's only when its payee names the merchant, or the company known to bill for that brand (ChatGPT bills as OPENAI LLC; YouTube Premium and Google One as GOOGLE). An app store's mandate (GOOGLE PLAY, APPLE) pays for any of the user's app subscriptions: it is never a brand's unless the user names the store. Never guess which mandate pays for what.
- Each card's buttons name its card, as Cancel (in “PAYEE”), Pause (in “PAYEE”): press only the one on the card you picked, and only the action the user asked for. A pause is not a cancellation.
- A card's Cancel is the last thing you press for a cancellation: the page then shows a QR that only the user's UPI app approves, with their UPI PIN, and the system sends it to them and asks; the task ends with their answer. There is no other button to press after it.
- Pause asks "Please select a date till when you'd like to pause it" with date chips (the next 15th and month-ends) and Custom date: pick the date the user gave, or ASK them for one. Then the page says to approve it in the UPI app: TELL the user once to approve it there with their UPI PIN, naming the payee and amount; the system sends them the QR's picture. Then WAIT; if nothing changes after a few waits, ASK whether they approved it, and DONE with their answer.
- When no card's payee is the merchant or its company: DONE saying no UPI AutoPay mandate for it was found, listing every payee with its amount and UPI app. Never press any card's buttons then.
- Transactions and complaints: UPI Transactions first asks "Confirm your account", the last 4 digits of the bank account, in four boxes: type [profile:bank_account_last4] there and press Verify & Continue. All Transactions then lists the last 60 days by month; Filters narrows them (app, payment type, date range, amount, mode); opening a row shows its status, Transaction ID, payment mode, debited account, UPI app, and Raise A Complaint.
- A complaint: open the transaction the user means (by payee, amount and date; ASK when two could be it), press Raise A Complaint, pick the reason that matches what the user said, add proof only if they gave one; the final submit needs the user's yes. Report the ticket number (CRN). My Complaints in the sidebar lists complaints with their status (ALL, UNDER REVIEW, ACTIONED).
- The user's UPI ID: the Default UPI Id chip on the home page (its Your UPI details table), or Stay Safe → UPI Number. De-link UPI Number only on the user's explicit request; it needs their yes.
- Checking a payee before paying: Stay Safe → Pay Safe, type the UPI ID, press Verify Payee, and report what MyUPI says. Pay no one.
- A general question about UPI (a forgotten PIN, a failed payment, frauds, an error code): the FAQs or Fraud Awareness answer it; else ask MyUPI's own assistant in "Ask the AI assistant…" and quote its answer. Never use that chat box to cancel, pause, or raise a complaint.
- Report what was done (payee, amount, frequency, UPI app, any reference shown); for a cancellation, that future UPI payments to it stop and the merchant's own account isn't closed by this.
