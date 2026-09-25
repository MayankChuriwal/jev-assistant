# Live smoke set

Most of this is automated now: `uv run jev --bench` runs the tasks in `bench/tasks.toml` up to the final approval and answers no (see the README). The runs below are the real completions, done with you in the chat.

v1 is done when each run below completes for real, on real sites, with your own accounts (spec §16). Runs 1–5 can spend money or send something to an organisation. Nothing is paid, booked, or submitted without your "Yes" on a summary card, so answer "No" whenever you only want to see how far a run gets.

## Before the first run

1. `uv sync`, then put `TYPESAFE_API_KEY` and `OPENAI_API_KEY` in `.env`.
2. `uv run jev probe`: each smoke site should say `ok`. It is worth knowing about a `captcha` or `http-403` before a run.
3. `uv run jev`, then open http://127.0.0.1:8766/.
4. The agent's Chrome starts logged out of everything. The first run on each site asks in chat for your phone number or email and an OTP; later runs reuse the login.
5. The first time a card is needed, the chat asks for its number, expiry, and CVV as hidden answers, and offers to keep them in this Mac's Keychain. macOS may show a Keychain prompt on this screen; answer it within 60 seconds, or the chat asks for the value instead.

## The runs

| # | Type this in chat (fill in the brackets) | Questions to expect | Pass |
|---|---|---|---|
| 1 | Order one Diet Coke 300 ml on Swiggy Instamart | login OTP, address, summary card, bank OTP | the order is confirmed and the result quotes its order id |
| 2 | Book an Uber from [pickup] to [destination] | ride types with prices, summary card | the ride is requested and the result quotes the trip |
| 3 | Order [dish] from [restaurant] on Swiggy | item options if the dish has any, address, summary card | the order is confirmed |
| 4 | Order [item] on Amazon.in | which listing, if several match; summary card | the order is placed and the result quotes the order number |
| 5 | Submit a complaint about [issue] on [the organisation's official form] | any details you haven't given, summary card | the result quotes the submission reference |
| 6 | Download my latest invoice from [site] | login, if needed | the document arrives in chat |
| 7 | Open http://127.0.0.1:8767/upload.html and submit the dispute with the files I send | the files (send them in chat) | the confirmation shows a reference and each file's SHA-256 |

Run 7 needs the fixture page served locally. In a second terminal:

    python3 -m http.server 8767 --bind 127.0.0.1 --directory assistant/fixtures

Each run passes when three things hold:
- the task ends `done`;
- the result quotes words from the page (an order id, "Order placed");
- every question it asked was one only you could answer.

## After each run

    uv run jev traces

This lists the latest tasks, each with its outcome, the number of questions asked, the step count, the time taken, and the evidence quoted. The full trace is a JSON file in `~/.jev/traces/`. It holds every step and decision with its probabilities. It holds no passwords, OTPs, or card details, and your name, phone, email, and UPI ID appear only as placeholders.

When a run fails or asks something it shouldn't, note the trace's file name and what you saw in chat. That goes into `docs/known-issues.md` and is fixed in the next plan.

## Results

| Date | Run | Trace | Pass? | Questions (needed / asked) | Notes |
|---|---|---|---|---|---|
| | | | | | |
