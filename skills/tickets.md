---
name: tickets
description: Book movie, show, or event tickets (BookMyShow)
inputs: movie or event, date, number of tickets
sites: bookmyshow.com
uses: location, payment, sign-in
summary: movie or event, venue, date and time, seats, number of tickets, fees, total
report: booking id, venue, date and time, seats, total
---
## Plan
1. Set the city the user is in with the site's city picker.
2. Find the movie or event; for "the most popular", take the first one the site lists as trending or top.
3. Choose the date, then the venue and show time asked for; ASK when several fit.
4. Choose the number of tickets and seats: seats together, in the category named, or ASK with the categories and prices.
5. Sign in when the site asks (usually before payment).
6. Choose the payment method the user picks; pay: the final control is the commit. Then report.
## Rules
- Dates like "tomorrow" or "this weekend" are resolved for you in dates; "evening" means shows from 5 pm.
- Convenience fees are part of the total: name them.
- Choose the show time from the chosen movie's own list of show times (a venue's page lists every movie's shows), and check that the page title names that movie before choosing seats.
- Select the number of seats asked, then proceed. Never toggle a seat on and off: if a seat won't select, pick another seat or row, or ASK.
