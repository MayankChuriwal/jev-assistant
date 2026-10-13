---
host: gyftr.com/instantvouchers
name: HDFC SmartBuy Instant Vouchers
aliases: SmartBuy, HDFC SmartBuy, Smart Buy, Instant Vouchers, GyFTR
start: https://www.gyftr.com/instantvouchers
learn: no
brand.swiggy: https://www.gyftr.com/instantvouchers/swiggy-gv-gift-vouchers
keep: tab
cart: Add to cart, View Cart & Buy
cards: .custom_dropdown .dropdown-menu li
offers: ACCELERATED REWARDS, INSTA DEALS
sign-in: auto
otp: Request OTP
step: Add to cart, View Cart & Buy, Request OTP, Continue, Submit, Read T&C, I agree to the Terms, Checkout
final: BUY NOW, Pay ₹, Redeem & Pay
checkout: Checkout, Redeem & Pay
agree: Read T&C and important instructions, I agree to the Terms and Conditions
never: Redeem Now, Hello
---
- HDFC SmartBuy's gift vouchers, sold by GyFTR. The home page lists the brands; "Search Brand" at the top finds one. A brand's page is https://www.gyftr.com/instantvouchers/BRAND-gift-vouchers (Swiggy's: https://www.gyftr.com/instantvouchers/swiggy-gv-gift-vouchers).
- A brand's page shows two offers: ACCELERATED REWARDS (up to 5X reward points or 5% cashback, ticked by default) and INSTA DEALS (an instant discount plus 1X points). Keep ACCELERATED REWARDS unless the user asked for the discount. Under "Choose Denomination" are the amounts (₹250, ₹500, ₹1,000, ₹2,000); the monthly limit and validity are shown above them.
- A goal to show a brand's vouchers ends with DONE listing every amount, the reward rate each offer states, and the monthly limit. It presses nothing: it never chooses an amount or adds to the cart.
- Never open the site's chat ("Hello, have a question? Let’s chat.") or press "Redeem Now" (it opens Swiggy's own site): neither is part of buying. A chosen amount stops being offered as a control; "Total Value" shows which one is chosen.
- The savings calculator ("Calculate Savings on your Card / Wallet", "Effective Price") is an estimate for one card: never report its figures as the price.
- To buy: for each amount the goal names, press it (it then shows "Total Value") and "Add to cart"; then "View Cart & Buy". An amount already in the cart shows a quantity stepper in place of "Add to cart". Each amount's count is set in the cart, with its line's own − and +.
- Add to cart asks to sign in: the system types [profile:phone] into "Enter 10 - digit mobile number", ticks "I agree to the Terms and Condition and Privacy Policy" and presses Continue; the code sent by SMS comes through the hidden field. A number already registered is asked "Enter Password": the system presses "Request OTP" instead, and that code comes through the hidden field too; never ask for the password.
- The cart (/instantvouchers/cart, also the cart count link at the top) shows "Deliver to:", the voucher, CART SUMMARY (Total Cart Value, You Save, Reward Points Earned), a promo code box (Apply), "You Pay", three boxes ("Read T&C and important instructions …" and "I agree to the Terms and Conditions", which the system ticks; "Receive Gift Voucher on WhatsApp", left alone unless the user asked), and Checkout, which opens GyFTR's payment page (pay.gyftr.com): the user's yes comes there, before paying. Checkout comes once the cart holds exactly the goal's vouchers. Its "Calculate Savings on your Card" menu (Infinia by default) only estimates savings and is no way to pay: the system picks the card the goal names there. The card is entered on the payment page after Checkout, so the cart has no payment method to pick: never ask the user how to pay here.
- Report the voucher, its amount and quantity, the amount payable and the points the page shows.
