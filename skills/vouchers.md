---
name: vouchers
description: Buy gift vouchers on a bank's voucher store, or read what a store offers for a brand
inputs: brand, amount
uses: payment
summary: vouchers, store, card, payable, reward
report: vouchers, order number
---
## Plan
1. On the store, open the brand's page: the store's search finds it. Take the brand's own main voucher (Swiggy's "Swiggy Money" or "Swiggy Gift Voucher"), never a sub-brand's ("Swiggy Instamart") unless the goal names it. A search suggestion that doesn't open the page: press the search button, or open the brand from the results.
2. A read (the goal says "Buy nothing"): put every amount the brand's page offers in amounts ("₹250, ₹500, ₹1,000, ₹2,000"); every amount needs reading, so scroll down when more are listed below; then the store's default offer only: its reward points, cashback or coins in reward as shown ("5X Reward Points"), or its instant discount in discount ("2.5% off"); any card fee in fee; the monthly or per-brand limit in limit. Then end with done. Two offers to choose between (GyFTR's ACCELERATED REWARDS and INSTA DEALS) are one or the other, never both: read the one ticked by default.
3. A purchase: choose each amount of the goal's vouchers and set its count; add them to the cart. Sign in when the store asks: [profile:phone] for the mobile number, [ask] for the code it sends; the store's note says any other way.
4. In the cart, read what it holds into cart as counts of amounts ("2 × ₹2,000 + 1 × ₹1,000") and the amount to pay into payable. Remove or lower anything the goal doesn't name. Tick the boxes the cart requires to agree to its terms; leave delivery by WhatsApp and any promotional box alone.
5. Go to payment: choose credit or debit card; type [ask] into the card number, expiry and CVV boxes, and the name on the card as the store's note says; leave "save card" unticked. Press the pay button: the system asks the user's yes itself. The bank's code goes in its box with [ask].
6. After paying, put the order's number in order_id exactly as the page shows it, then end with done.
## Rules
- A read ends with done as soon as the brand's page shows its amounts: a fact the page doesn't show (a limit, a fee) stays null; never open offer panels, terms or other pages to look for one. A read presses no amount, cart, buy or pay control, and signs in to nothing. When the brand's prices show only after a sign-in, or a captcha stands in the way, end with stop saying so. When the store doesn't sell the brand, end with stop saying so.
- Never copy a voucher code or PIN into a fact or into say: the user gets the codes by SMS and email.
- The GyFTR stores (www.gyftr.com/…: SmartBuy, SBI Card, Axis Gift EDGE, IndusInd, Yes Bank) share one layout: a brand page with ACCELERATED REWARDS (the default; keep it unless the goal says otherwise) and INSTA DEALS offers, "Choose Denomination", "Add to cart", sign-in by mobile number and a code, a cart with each voucher's − and +, "Read T&C …" and "I agree …" boxes and Checkout, then GyFTR's payment page (pay.gyftr.com) with Card and PROCEED.
- Never use a wallet, reward points, iCash, coins or a promo code unless the goal says so.
- A limit reached, a brand out of stock, or a payment declined ends with stop, quoting the page.
