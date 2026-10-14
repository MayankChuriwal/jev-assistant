---
name: vouchers
description: Buy gift vouchers on a bank's voucher store, or read what a store offers for a brand
inputs: brand, amount
uses: payment
summary: vouchers, store, card, payable, reward
report: vouchers, order number
---
## Plan
1. On the store, open the brand's page: type the brand into the store's search box, wait a moment, then press the suggestion under the box that names the brand's voucher. The box's search icon often only clears it; when the suggestions aren't offered as their own elements (they show only in the box's own label), press ArrowDown then Enter in the box; when no suggestion shows, press Enter in the box. When the search finds nothing ("couldn't find a match"), find the brand another way: try its other spelling ("Big Basket" for "BigBasket"), then open its category ("Grocery and F&B", "Travel") or the store's list of brands, and open the brand from there. A brand with several vouchers on the store ("MakeMyTrip e-Pay", "MakeMyTrip Hotel e-Pay", "MakeMyTrip Holiday e-Pay"): take its general one (the plain brand name) unless the goal names another; never ask which. Take the brand's own main voucher (Swiggy's "Swiggy Money" or "Swiggy Gift Voucher"), never a sub-brand's ("Swiggy Instamart") unless the goal names it.
2. A read (the goal says "Buy nothing"): put every amount the brand's page offers in amounts ("₹250, ₹500, ₹1,000, ₹2,000"); every amount needs reading, so scroll down when more are listed below; then the store's default offer only: its reward points, cashback or coins in reward as shown ("5X Reward Points"), or its instant discount in discount ("2.5% off"); any card fee in fee; the monthly or per-brand limit in limit. Then end with done. Two offers to choose between (GyFTR's ACCELERATED REWARDS and INSTA DEALS) are one or the other, never both: read the one ticked by default. A discount only for another way to pay (UPI, net banking, a wallet) is not the card's: leave it out.
3. A purchase: choose each amount of the goal's vouchers and set its count (a store that takes any amount in a range: type the voucher's amount into its amount box); add them to the cart. Sign in when the store asks: [profile:phone] for the mobile number, [ask] for the code it sends; the store's note says any other way.
4. In the cart, read what it holds into cart as counts of amounts ("2 × ₹2,000 + 1 × ₹1,000") and the amount to pay into payable. Remove or lower anything the goal doesn't name. Tick the boxes the cart requires to agree to its terms; leave delivery by WhatsApp and any promotional box alone.
5. Go to payment: choose credit or debit card; type [ask] into the card number, expiry and CVV boxes, and the name on the card as the store's note says; leave "save card" unticked. Press the pay button: the system asks the user's yes itself. The bank's code goes in its box with [ask].
6. After paying, put the order's number in order_id exactly as the page shows it, then end with done.
## Rules
- A read ends with done as soon as the brand's page shows its amounts: a fact the page doesn't show (a limit, a fee) stays null; never open offer panels, terms or other pages to look for one. A read presses no amount, cart, buy or pay control, and signs in to nothing. When the brand's prices show only after a sign-in, or a captcha stands in the way, end with stop saying so. When the store doesn't sell the brand, end with stop saying so.
- Never copy a voucher code or PIN into a fact or into say: the user gets the codes by SMS and email.
- The GyFTR stores (www.gyftr.com/…: SmartBuy, SBI Card, Axis Gift EDGE, IndusInd, Yes Bank) share one layout: a brand page with ACCELERATED REWARDS (the default; keep it unless the goal says otherwise) and INSTA DEALS offers, "Choose Denomination", "Add to cart", sign-in by mobile number and a code, a cart with each voucher's − and +, "Read T&C …" and "I agree …" boxes and Checkout, then GyFTR's payment page (pay.gyftr.com) with Card and PROCEED.
- Never use a wallet, reward points, iCash, coins or a promo code unless the goal says so.
- A limit reached, a brand out of stock, or a payment declined ends with stop, quoting the page.
