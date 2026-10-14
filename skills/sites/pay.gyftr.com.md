---
host: pay.gyftr.com
name: GyFTR payment page
aliases: PG Container, Multi Gateway Payment
start: https://www.gyftr.com/instantvouchers
learn: no
step: Card
final: PROCEED
---
- GyFTR's payment page for its stores' orders ("PG Container - Multi Gateway Payment"): Bill Details (Bill Value, Order Id, Mobile Number), "Payable Amount", then the payment methods: Card (its boxes "Valid Card Number", "Name On Card", "Expiry", "CVV No.", and "Save card securely for your future transactions") and Wallet, and PROCEED.
- Card: type [ask] into the card number, expiry and CVV boxes and [profile:name] into "Name On Card"; leave "Save card securely …" unticked; never use Wallet.
- PROCEED pays: the system shows the user the order and the payable amount and asks their yes. Then the user's bank asks for a one-time password for this payment: type [ask] into its box and press its submit.
- After paying, GyFTR's page shows the order: put its order number in order_id.
