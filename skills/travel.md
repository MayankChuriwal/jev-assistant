---
name: travel
description: Search or book flights, trains, buses, and hotels (Google Flights, MakeMyTrip)
inputs: from, to, date
sites: google.com/travel/flights, makemytrip.com
uses: sign-in, payment
summary: route or hotel, date and time, airline or room, travellers, fare, payment method
report: booking reference, route or hotel, date and time, fare
---
## Plan
1. Set the trip type first: one-way for one traveller unless the user mentions a return or more people.
2. Enter from, to, and the date (resolved dates are in dates).
3. Search; sort or filter the way the goal asks (cheapest, non-stop, rating above a number) with the site's own sort and filter controls.
4. For a question: DONE with the answer (airline, departure time, price; or hotel, rating, price per night), restating the route and date.
5. To book: ASK before choosing a fare or room that differs in price; fill traveller details from the profile and the user's answers; pay: the final control is the commit. Then report.
## Rules
- Never report a round-trip price for a one-way question: check the trip type after every search (a reload can reset it).
- "Cheapest" is the lowest price after sorting by price, not the first result.
- A hotel's nightly price and its total differ: say which one you give.
- Close an open date or guest picker (Escape) before pressing Search; the search is done only when results show.
- For a question, close a login pop-up instead of signing in.
