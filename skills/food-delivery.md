---
name: food-delivery
description: Order restaurant food for delivery (Swiggy, Zomato)
inputs: dish, restaurant?
sites: swiggy.com, zomato.com
uses: location, payment, sign-in
summary: dishes with quantity and options, restaurant, delivery address, payment method, fees, total
report: order number, restaurant, arrival time, total
---
## Plan
1. Sign in when the site asks.
2. Set the delivery location first.
3. Open the restaurant the user named through the site's search. When they named only a dish, search the dish and ASK which of the top restaurants, one line each with rating, delivery time, and the dish's price. Choose restaurants from the dish's search results (or results narrowed to the dish), never from the general restaurant list, and check the dish is on the chosen restaurant's menu before adding it.
4. Open the dish and choose the options the user named (size, crust, add-ons); ASK about options that change the price and weren't named.
5. Add it to the cart, open the cart and its bill, and note every fee.
6. Choose the payment method the user picks.
7. Place the order: the final control is the commit. Then report.
## Rules
- A closed restaurant or a dish that isn't available: TELL the user and ASK for an alternative (another outlet, another dish).
- The cart may hold items from before: never remove them unless the user says so; ASK when it holds another restaurant's items.
- Name fees the user could avoid before the final step.
