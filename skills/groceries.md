---
name: groceries
description: Buy groceries or everyday items for quick delivery (Swiggy Instamart, Zepto, Blinkit)
inputs: item, quantity?
sites: swiggy.com, zeptonow.com, blinkit.com
uses: location, payment, sign-in
summary: items with quantity, delivery address, payment method, fees, total
report: order number, arrival time, total
---
## Plan
1. Sign in first when the site asks before showing the cart or checkout.
2. Set the delivery location before searching (see the location skill).
3. Search for the item in the user's words; the site's search address, when its note gives one, is fastest.
4. Open the product that matches: the brand and product named, in the size named or the closest size of the same product.
5. Add the quantity asked for (one when the user said none).
6. Open the cart and its detailed bill; note every fee.
7. Choose the payment method the user picks (see the payment skill).
8. Place the order: the final control is the commit.
9. Report the order number, arrival time, and total.
An order needs all of steps 6 to 9: when you replan, the plan must still end with placing the order (the commit) and the report.
## Rules
- A service that sells both food and groceries keeps them in separate sections with separate search boxes: search groceries only in the grocery section (the site's search address, when its note gives one), never in the restaurant search.
- Keep the brand and product the user named. If that exact size isn't sold, take the closest size of the same product and say so; never switch brand or product without asking.
- The cart may already hold the user's own items: never remove them unless the user says so.
- Name any fee the user could avoid (a small-cart fee, a delivery fee under a free-delivery amount) and ASK whether to add items to drop it, with a concrete option, before the final step.
