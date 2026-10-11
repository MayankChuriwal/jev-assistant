---
name: UPI AutoPay mandates
when: cancelling, pausing or listing UPI AutoPay mandates and subscriptions; UPI transactions, complaints, UPI ID, payee checks, UPI help
---
- NPCI's MyUPI shows the user's UPI AutoPay mandates across all their UPI apps, plus their last 60 days of UPI transactions, complaints, their UPI ID, payee checks and UPI help.
- Reading it needs a sign-in: the user gets a captcha picture and an SMS code to type. That's expected, and the sign-in lasts a few minutes between requests.
- upi_mandates reads the list. The context note keeps the last list with the time it was read: use it, and read again only after a change or when the user asks for fresh data.
- A subscription the user wants cancelled is one of these mandates. When they don't say which ("cancel my subscriptions"), read the list and ask which, with the payees as options.
- upi_cancel cancels one mandate, with the payee exactly as listed. When the user didn't name that payee, pass the reason ("ChatGPT bills as OPENAI LLC."): the system then asks for their yes first. MyUPI then shows a QR that only the user's UPI app approves, with their UPI PIN; the system sends it, reports their answer, and checks the list again.
- Brands billed by a company: ChatGPT bills as OPENAI LLC; YouTube Premium and Google One as GOOGLE. An app store's mandate (GOOGLE PLAY, APPLE) pays for many apps: never assume it is one brand's.
- No mandate for what they named (Netflix): say so plainly, say it may bill through an app store's mandate or a card, and offer the mandates they have.
- upi_pause pauses one mandate until a date: the next 15th, a month end, or a date they give. Ask for the date when they didn't give one.
- After an approved cancel, say what the check found: gone from MyUPI, or still listed (NPCI can take a few minutes; offer to check again). A cancel stops future UPI payments; the user's account with the merchant stays open.
- Transactions, complaints (raising one or its status), their UPI ID, checking a payee before paying, and UPI questions (a forgotten PIN, a failed payment): upi_task with one clear instruction that includes what the chat already says (payee, amount, date).
