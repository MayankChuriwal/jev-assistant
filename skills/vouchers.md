---
name: vouchers
description: Buy gift vouchers on a bank's voucher store, or read what a store offers for a brand
inputs: brand, amount
uses: payment
summary: vouchers, store, card, payable, reward
report: vouchers, order number
---
## Plan
1. On the store, open the brand's page: the store's search finds it.
2. A read (the goal says "Buy nothing"): put every amount the brand's page offers in amounts ("₹250, ₹500, ₹1,000, ₹2,000"), the reward the page states for its default offer in reward as shown ("5X Reward Points"), any instant discount in discount, any card fee in fee, the monthly or per-brand limit in limit. Then end with done.
3. A purchase: choose each amount of the goal's vouchers and set its count; add them to the cart. Sign in when the store asks: [profile:phone] for the mobile number, [ask] for the code it sends; the store's note says any other way.
4. In the cart, read what it holds into cart as counts of amounts ("2 × ₹2,000 + 1 × ₹1,000") and the amount to pay into payable. Remove or lower anything the goal doesn't name. Tick the boxes the cart requires to agree to its terms; leave delivery by WhatsApp and any promotional box alone.
5. Go to payment: choose credit or debit card; type [ask] into the card number, expiry and CVV boxes, and the name on the card as the store's note says; leave "save card" unticked. Press the pay button: the system asks the user's yes itself. The bank's code goes in its box with [ask].
6. After paying, put the order's number in order_id exactly as the page shows it, then end with done.
## Rules
- A read presses no amount, cart, buy or pay control, and signs in to nothing. When the brand's prices show only after a sign-in, or a captcha stands in the way, end with stop saying so. When the store doesn't sell the brand, end with stop saying so.
- Never copy a voucher code or PIN into a fact or into say: the user gets the codes by SMS and email.
- The GyFTR stores (www.gyftr.com/…: SmartBuy, SBI Card, Axis Gift EDGE, IndusInd, Yes Bank) share one layout: a brand page with ACCELERATED REWARDS (the default; keep it unless the goal says otherwise) and INSTA DEALS offers, "Choose Denomination", "Add to cart", sign-in by mobile number and a code, a cart with each voucher's − and +, "Read T&C …" and "I agree …" boxes and Checkout, then GyFTR's payment page (pay.gyftr.com) with Card and PROCEED.
- Never use a wallet, reward points, iCash, coins or a promo code unless the goal says so.
- A limit reached, a brand out of stock, or a payment declined ends with stop, quoting the page.
