# Voucher demo: implementation tasks

Spec: `docs/superpowers/specs/2026-10-13-voucher-demo-design.md`. Executed inline, test-first, one commit per task; the
unit and browser suites pass before each commit.

- [x] 1. `final:` site-note flag: a control whose label starts with one of its comma-separated words is a commit
      outright (`gate.commit_label`). (a5fe4c0)
- [x] 2. A purchase approval offers Yes and No; a plain No ends the task: outcome `not_approved`, "You chose not to
      pay. Nothing was charged."; any other answer keeps today's Retry. (ae1a7ef)
- [x] 3. Brain: `PORTALS`, `voucher_options`, `voucher_checkout` (portal and brand the user named, read amounts in
      code, no re-read, `not_approved` mapped), playbook `skills/playbooks/vouchers.md`. (4adc5e7)
- [x] 4. Site notes for SmartBuy (GyFTR), iShop and ICICI's net banking sign-in; `icici_user_id` asked in the hidden
      field. (4adc5e7)
- [x] 5. Starter card "Buy a voucher". (4ae0843)
- [x] 6. Brain evals: three voucher cases; 15/15 with the real model. (4ae0843)
- [x] 7. Dry run on the live sites over an empty throwaway memory (so no OTP was sent): both portals reach their
      sign-in. Fixes it found:
      - a restart ends sign-ins left open on `keep: page` sites too (7f1c488);
      - a voucher read is a lookup ("Buy nothing" read as an order) and opens the brand's own page, `brand.swiggy:`
        (7b8019d), listing the amounts as the page shows them (705f845);
      - `learn: no` on a note keyed by a path (SmartBuy's) stops the learn offer (615182a);
      - the worker presses the amount and the note's `cart:` button itself, and `step:` names controls that only lead
        on (iShop's Buy now) so no yes is asked before the price (ddad177);
      - the amount's Total and the cart button are scrolled into view when off screen (e9b6f3f).

Past the sign-in, the whole chain ran on a local replica of SmartBuy's brand page (copied from the live one) and its cart page (worded from GyFTR's public scripts: CART SUMMARY, Pay via Reward Points, Deliver to:, You Pay, BUY NOW; 58f2c2f, 80f4518). Final run: ₹500 → Add to cart → View Cart & Buy → the approval card at "BUY NOW" with no payment question → No → "Nothing was charged". An earlier run on a guessed cart page: with the real brain, worker, Jev and gate: ₹500 → Add to cart → View Cart & Buy → the approval card at "Proceed to Pay ₹500" (Yes / No) → No → "You chose not to pay. Nothing was charged."; the replica's payment page was never requested.

iShop's chain ran the same way on a replica (its brand page as mapped live, its checkout worded from iShop's public scripts, signed in already): ₹1000 → Buy now → "Choose Payment Mode" asked (Credit Card) → the approval card at "Proceed to Pay" → No → "Nothing was charged"; its payment page was never requested. Known limit for a real purchase (out of this demo's scope): the payment mode the user named is not ticked on the page before the approval.

Live with the user (needs their sign-in): SmartBuy's OTP, cart and checkout up to the approval card; iShop's net
banking sign-in (User ID, password, any OTP), checkout, its payment-mode question, and the approval card.
