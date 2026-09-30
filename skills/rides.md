---
name: rides
description: Book a cab, auto, or bike ride (Uber, Ola, Rapido)
inputs: pickup, drop
sites: uber.com, olacabs.com, rapido.bike
uses: location, payment, sign-in
summary: ride type, pickup → drop, fare, payment method
report: driver, car, number plate, PIN, arrival time, fare
---
## Plan
1. Sign in when the site asks.
2. Set the pickup: the user's words, or their current location through the site's detect-location option.
3. Set the drop: type the user's words and click the matching suggestion.
4. When the ride options show, ASK which one, one line per option with its fare and arrival time, unless the user named one.
5. Choose the payment method the user picks.
6. Request the ride: the final control is the commit; the system watches until a driver is assigned.
7. Report the driver, car, number plate, PIN, and arrival time.
## Rules
- Pick the exact place the user named (a specific terminal, gate, or building), never a broader one.
- A pre-selected ride type is not the user's choice.
- Pickup and drop boxes can share a label: fill the pickup first, then the drop.
