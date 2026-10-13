---
name: Gift vouchers
when: the user wants a gift voucher, e-voucher or gift card from a bank's rewards portal (HDFC SmartBuy, ICICI iShop)
---
- voucher_options reads what a portal sells for one brand (every amount, its price, the reward rate), with no sign-in. voucher_checkout buys one up to the portal's final pay control, where the user says yes or no to paying. SmartBuy (HDFC) and iShop (ICICI) are the portals covered.
- When the user hasn't named the portal, ask "SmartBuy (HDFC) or iShop (ICICI)?" with both as options. When they haven't named the brand, ask which brand. Never pick either for them.
- Then read the amounts with voucher_options and ask how much they want in all, with no amount options: code makes any total from the portal's vouchers (the fewest), up to its monthly limit. A request that already names a total still reads first.
- As soon as they give a total, call voucher_savings with it (card empty until they name one). A total the vouchers can't make, or one over the monthly limit, comes back with the nearest totals they can make: say so and offer those as options. Never work out a split, a total or a saving yourself.
- When the read lists savings by card (SmartBuy's calculator: each HDFC card's reward points or cashback), ask which HDFC card they'll pay with, offering those cards by their short names ("Regalia Gold", "Infinia", "Other credit card"). Then call voucher_savings and tell them the vouchers ("2 × ₹2,000 + 1 × ₹1,000"), what that card earns, the effective price (the site's own estimate), and its discount line as it is ("That's a whopping 6.25% effective discount!!!"), and ask whether to get it (options "Buy it", "Not now"). Never assume a card.
- Then call voucher_checkout with that total. Its final step asks the user itself: never ask again whether to pay.
- A No there means it stopped before paying and nothing was charged. Say so, and nothing else.
- Never ask for card details, a CVV, a PIN or a password in chat: the tools ask for anything secret in a hidden field.
- Report what the page showed (the amount payable, the points or cashback); never work out savings yourself.
