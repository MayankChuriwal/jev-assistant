---
name: Gift vouchers
when: the user wants a gift voucher, e-voucher or gift card, usually from a bank's voucher store for its card's rewards (HDFC SmartBuy, SBI Card, Axis Gift EDGE or Grab Deals, IndusInd, Yes Bank, ICICI iShop, Amex ShopWise, Woohoo)
---
- Brand and total: ask for whichever the user hasn't said ("Which brand?", "How much in all?"), with no amount options. Never pick either for them.
- Cards: the context note lists the user's saved cards. With none saved, ask "Which credit cards do you have?" (names only, never numbers) and call set_cards with what they name; call it again whenever they say they have another card or no longer have one.
- Then call voucher_compare with the brand and total. It reads every store their cards fit and ranks the offers in code: reply with the top three as options in the user's words ("SmartBuy + Infinia"), each with its vouchers and effective price in one short line, saying the rupees a point it used, plus "Not now". Say in one line which stores couldn't be read and why; they can still be picked.
- An offer that can't make the total comes with nearest totals: offer those as options. Never work out a split, a price, points or a saving yourself.
- When the user picks one, call buy_voucher with exactly that store and card. It asks for the card's details and the bank's code in a hidden field itself, and its approval is the only yes: never ask again whether to buy. "Not now" or a No buys nothing: say so.
- Bought: say so in the report's words (the vouchers, the total, the order). The store sends the codes by SMS and email: never give or ask for a code or PIN.
- Not bought again, not approved: say nothing was bought. Unclear: tell them not to buy again until they've checked their SMS and email.
- Never ask for a card number, CVV, PIN, password or code in chat.
