---
host: pay.gyftr.com
name: GyFTR payment page
aliases: PG Container, Multi Gateway Payment
start: https://www.gyftr.com/instantvouchers
learn: no
pay-form: Valid Card Number, Name On Card, Expiry, CVV
step: Card
final: PROCEED
never: Save card, Wallet
---
- GyFTR's payment page for a SmartBuy order ("PG Container - Multi Gateway Payment"), opened by the cart's Checkout: Bill Details (Bill Value, Order Id, Mobile Number), "Payable Amount", then the payment methods: Card (its boxes "Valid Card Number", "Name On Card", "Expiry", "CVV No.", a "Save card securely for your future transactions" box) and Wallet, and PROCEED.
- The system fills the card form itself: the name on the card from [profile:name]; the card number, expiry and CVV from the user through the hidden field, each time, never kept. "Save card securely …" stays unticked and Wallet is never used.
- PROCEED pays: the system shows the user the order and the payable amount and asks once before it. A payable amount above the goal's total is never paid.
- After PROCEED the user's bank asks for a one-time password for this payment: it comes from the user through the hidden field, and sending it needs no second yes.
- Report what the page shows when it ends: the order id, and whether the payment went through.
