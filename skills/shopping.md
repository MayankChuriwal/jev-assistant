---
name: shopping
description: Buy a product, or find its price, on a shopping site (Amazon, Flipkart, Myntra)
inputs: product
sites: amazon.in, flipkart.com, myntra.com
uses: payment, sign-in
summary: product with variant and quantity, seller, delivery address, delivery date, payment method, total
report: order number, delivery date, total
---
## Plan
1. Search for the product in the user's words.
2. Open the listing that is the product named (brand, model, size or storage); skip sponsored results that aren't it.
3. For a price question: DONE with the price the product page shows and the exact variant it belongs to.
4. To buy: choose the variant the user named; ASK about variants that change the price (colour, size, storage).
5. Use Buy Now, so only this item is bought.
6. Check the delivery address and date; choose the payment method the user picks.
7. Place the order: the final control is the commit. Then report.
## Rules
- "Cheapest", "lowest", "best", or "earliest" means sorting by that with the site's own sort, or checking every candidate shown; never take the first result as it stands.
- Never remove items from the user's cart.
- When two listings are the same product, prefer the one sold or fulfilled by the site itself, and say which seller.
