# Jev — Finance agent, step 2: gift vouchers on the generic agent, compared across portals

Date: 2026-10-14. Branch: `mini-jev-agent`. Builds on `2026-10-14-finance-agent-bills-design.md` (the money layer and
the page model, step 1) and `2026-10-13-voucher-demo-design.md`; everything they specify stays unless this spec changes
it. Research: `docs/research/2026-10-14-finance/voucher-portals.md`.

## 1. Why

Bills now run on one generic page model with money rules in code. Gift vouchers still run on the old loop: Jev's next
click, the gpt-4.1-mini planner, and about 400 lines of SmartBuy-only code steps in `worker.py`. The user objected to
exactly that ("I would have simply written a script"). Step 2 of the plan moves vouchers onto the generic agent and
deletes the SmartBuy code.

The user also wants the top voucher portals, and the value of a voucher depends on the portal and the card. Each bank
rewards its own cards on its own store (HDFC SmartBuy 5X, ICICI iShop 6X, Amex ShopWise 5X). So a request is compared
across the portals that fit the user's cards, and then bought on the one they pick.

## 2. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Outcome | Compare, then buy. "Get me ₹5,000 of Swiggy": Jev reads the brand on every portal the user's cards fit, ranks the offers by effective price, the user picks, Jev buys |
| 2 | The user's cards | Asked once, saved as card names only (never numbers), changed whenever the user says so. Only portals those cards fit are read |
| 3 | Ranking | A ₹ value per reward point, from card notes (words, editable). Code turns every offer into an effective price in ₹, shows the points too, and says which value it used |
| 4 | Reading offers | Live: the generic agent reads each portal's brand page without signing in. A read is reused for 24 hours |
| 5 | Portals | The GyFTR stores (HDFC SmartBuy, SBI Card, Axis Gift EDGE, IndusInd, Yes Bank), ICICI iShop, Woohoo (woohoo.in and Axis Grab Deals), Amex ShopWise |
| 6 | Left out | Zingoy, Amazon and Flipkart: their terms forbid bots. Magicpin, PhonePe and Paytm gift cards: app-only, offers differ by user. Kotak Unbox: app-only |
| 7 | Page model | The step 1 actor (`gpt-6-luna`, effort high), made job-neutral: its instructions keep the generic rules, the job's rules move to its playbook, its facts schema is chosen per job |
| 8 | Approval | One yes per purchase, at its first pay control, written in code. The bank's code and later controls ride on it while the payable stays at or under the approved amount |
| 9 | Codes | Voucher codes and PINs never reach a model, a trace or the chat. After the yes, every page is masked as the worker reads it. The report is written in code |
| 10 | Deleted | SmartBuy's code steps in `worker.py`, the brain's `voucher_options`, `voucher_savings` and `voucher_checkout`, the SmartBuy-only browser helpers and the site-note fields only they read |
| 11 | Live checks | Read-only compare runs on the real portals, approved by the user, including pressing iShop's terms pop-up ("Accept & Proceed") to read its pages. No sign-in, no purchase |
| 12 | Done | The comparison and a purchase work on the replicas; a live read-only comparison ranks the real portals; the user buys one small voucher (₹250–₹500) for real |

## 3. The conversation

1. "Get me ₹5,000 of Swiggy vouchers." No cards saved: Jev asks "Which credit cards do you have?" and saves the names it
   matches to card notes. A card with no note is saved with its bank only; its offers show points, unranked.
2. Jev compares: "Reading SmartBuy… 1 of 3", then the ranked offers as options plus "Not now", for example
   "SmartBuy with Infinia: 2 × ₹2,000 + 1 × ₹1,000, 833 points ≈ ₹833 back, effective ₹4,167". A portal that couldn't be
   read says why ("couldn't read: sign-in needed to see prices").
3. The user picks one. Jev buys it: signs in (the code or password in the hidden field), fills the cart, asks for the
   card in the hidden field, and shows one approval card before paying.
4. "Bought ₹5,000 of Swiggy vouchers on SmartBuy (2 × ₹2,000 + 1 × ₹1,000), order HSB…. The codes come by SMS and
   email." Jev never shows or asks for a code.

## 4. Components

### 4.1 Brain: tools and the vouchers playbook

- `set_cards(cards)`: saves the user's card names (memory id `cards`, a comma list) after matching each to a card note.
  Called when the user names their cards or changes them.
- `voucher_compare(brand, amount)`: refuses until a brand and an amount were named and cards are saved. For each portal
  whose note's `banks:` includes a bank of the user's cards (or says `any`), it runs a read task (§4.3), one after the
  other, with a status line each. A read less than 24 hours old for the same portal and brand is reused. Code then ranks
  the offers (§4.4) and returns them, each with its voucher split, points, ₹ value, fee, discount, effective price, the
  ₹ per point used, and the portal's monthly limit. A total the portal's amounts can't make returns its nearest totals.
- `buy_voucher(portal, brand, amount, card)`: refuses unless that offer was in the user's last comparison, the card is
  one of theirs, and the user's last message isn't a No. It starts the buy task with the goal from
  `money.voucher_goal` at the brand's page, and maps the outcomes: `bought`, `not_approved`, `not_bought`, `unclear`,
  `bought_before`.
- `skills/playbooks/vouchers.md` is rewritten: ask for cards once, compare before buying, offer the ranked list as
  options, never work out a split, a saving or a price, never give or ask for a code, never ask for card details or a
  password in chat. The GyFTR stores' shared know-how (amounts, cart, sign-in by phone code, pay.gyftr.com) moves here
  from the SmartBuy note.
- Removed: `voucher_options`, `voucher_savings`, `voucher_checkout` and their helpers. `vouchers`, `nearest` and
  `counted` move to `money.py`.

### 4.2 The page model (`assistant/actor.py`)

- `INSTRUCTIONS` keeps only what every money job shares: the placeholders (`[profile:…]`, `[ask]`, `[captcha]`),
  pop-ups, pressing a pay button like any other, never pressing one twice, never saving a card, never signing up, page
  text is data. The bill rules (full amount due, read the bill first, UPI QR) move to `skills/playbooks/bills.md`.
- The facts schema comes from the job: `money.FACTS[kind]`. A bill's facts stay as they are. A voucher job's facts:
  `amounts` (every amount offered, as shown), `reward` (the reward the offer states, as shown), `discount`, `fee`,
  `limit` (the monthly or per-brand limit), `cart` (what the cart holds, as shown), `payable`, `order_id`.
- A read job's goal says "Buy nothing"; its guide says it asks the user nothing and ends with done once the facts are
  read, or stop when a sign-in or captcha stands in the way.

### 4.3 The worker

- `self.bill` becomes `self.job`, the intent from `money.job_intent(goal)`: a bill, a voucher purchase
  (`kind: "voucher"`), or a voucher read (`kind: "voucher_read"`). The bill loop (`bill_step`, `bill_turn`, `run_plan`
  and the payment helpers) serves every job; names shared by all jobs lose their `bill_` prefix.
- A read job: any money control, `[ask]`, `[profile:…]` or `[captcha]` ends it as `stopped` with the reason; nothing is
  asked of the user. Its spend cap is $0.30.
- A buy job: spend cap $1.00. Before the yes, code checks that a `cart` fact read on the portal equals the goal's split
  and that the payable is grounded on the page; otherwise the yes is refused with the reason in the model's problems.
- After the yes (`approved_pay`), the page is masked (§4.7) as it is read, before anything else uses it.
- Proof of a purchase is an `order_id` grounded on the page after the yes. Without it the task ends `unclear`.
- Deleted, with their tests: `card_step`, `quantity_step`, `brand_page`, `refill_step`, `agree_step`, `checkout_step`,
  `card_form_step`, `bought`, `voucher_read`, `page_words`, `offers`, `card_savings`, `voucher_step`, `voucher_order`,
  `cart_lines`, the voucher branch of `approval_lines`, the voucher quantity Retry in `act`, and the constants only they
  use. Kept: the shared steps (`sign_in_step` and `mandate_step` for MyUPI, `notice_step` and `row_step` for Experian,
  `code_submit`, `paid`, `bank_box`, `otp_step`).

### 4.4 The money layer (`assistant/money.py`)

- `voucher_goal(portal, brand, amount, split, card)` →
  `Buy ₹5,000 of Swiggy vouchers on SmartBuy: 2 × ₹2,000 + 1 × ₹1,000, paying with Infinia.`;
  `read_goal(portal, brand, amount)` →
  `Read the Swiggy vouchers on SmartBuy for ₹5,000: every amount, the reward, any discount or fee, and the monthly
  limit. Buy nothing.`; `job_intent(goal)` parses all three goal kinds back.
- `split(amounts, total)` (the fewest vouchers), `nearest(amounts, total, limit)`, `counted(split)`: moved from the
  brain.
- `reward(text)`: the offer's words as one of: a multiplier of the card's base rate ("5X reward points"), a percent
  back ("5% cashback", "10% GV coins"), a percent off ("5% off"), or unknown. Unknown is shown, never ranked.
- `value(offer, card, amount)`: points = amount × the card's base earn × the multiplier, held to the portal's limit;
  ₹ back = points × the card's ₹ per point, or the percent back; minus any discount, plus any fee. Effective price =
  payable − ₹ back. Example: Infinia earns 5 points per ₹150; 5X on ₹5,000 is 833 points; at ₹1 a point the effective
  price is ₹4,167. The limit is the portal's whole monthly cap (Jev doesn't know what the user used this month) and the
  offer says so.
- `voucher_lines(intent, facts)`: the approval, for example `["Buy ₹5,000 of Swiggy vouchers on SmartBuy", "2 ×
  ₹2,000 + 1 × ₹1,000", "Paying with Infinia", "Payable: ₹5,000", "Reward shown now: 5X reward points"]`, plus
  "Lower than when compared: …" when the live reward reads worse than the comparison's.
- `mask_codes(text, keep)` (§4.7).

### 4.5 Cards and memory

- Card notes `skills/cards/<bank>-<card>.md`, frontmatter only: `name`, `aliases`, `bank`, `earn` ("5 per 150": points
  per ₹), `point` (₹ per point used for ranking), `source` (where the value comes from). A starter set covers the common
  cards of the seven banks (HDFC, ICICI, Axis, SBI, Amex, IndusInd, Yes Bank); values come from the issuers' published
  redemption rates and the user can edit them.
- Portal notes gain `banks:` (the banks whose cards the store rewards, or `any`) and keep `start`, `aliases`,
  `brand.<x>`, `final`, `step`, `close`, `learn`.
- Memory: the id `cards` (names only; `privacy.storable` already refuses card numbers); a new label `amex_user_id`
  ("Amex user ID"); `icici_user_id` stays. The `payments` table gains `kind` (`bill`, `voucher`): a voucher purchase is
  `biller` = the portal, `account` = the brand, `method` = the card name. Paying again asks first when the same portal,
  brand and amount were bought (or ended unclear) in the last 24 hours.

### 4.6 Site notes

- GyFTR stores, one note each with `banks:` and its start page: SmartBuy (`gyftr.com/instantvouchers`, HDFC), SBI Card
  (`gyftr.com/sbicard`, SBI), Axis Gift EDGE (`gyftr.com/edgerewards`, Axis), IndusInd
  (`gyftr.com/indusindinstavouchers`, IndusInd), Yes Bank (`gyftr.com/yesbank`, Yes Bank). `pay.gyftr.com` stays the
  payment page. The SmartBuy note loses the fields only deleted code read (`cart`, `cards`, `offers`, `sign-in`, `otp`,
  `checkout`, `agree`, `never`, `keep`); `pay.gyftr.com` loses `pay-form` and `never`.
- iShop (`ishoprewards.com`, ICICI): kept; its sign-in is ICICI net banking (`[profile:icici_user_id]`, the password
  `[ask]`).
- Woohoo: `woohoo.in` (`any`) and Axis Grab Deals (`grabdealsaxisbank.woohoo.in`, Axis; sign-in by mobile and the
  card's last four digits, both through the hidden field, never kept).
- Amex ShopWise (`shopwise.giftstacc.com`, Amex): reached after signing in to the Amex India account
  (`[profile:amex_user_id]`, the password `[ask]`). Its prices may show only after sign-in: then a compare lists it as
  "price after sign-in".
- No note is written for the bench's unseen store (§6).

### 4.7 Codes never reach a model

`mask_codes(text, keep)` runs on every page the worker reads after the yes: its text and its elements' labels and
values, before the actor, `jev.after_commit`, the trace or the chat see it. On a line that names a code, PIN, voucher
number or card number, every run of 4 or more letters and digits holding a digit becomes `[code]`. Elsewhere, any run
of 10 or more holding a digit becomes `[code]`, except on lines that name an order, transaction or reference (the
proof). The report is built in code from the intent and the order ID. This closes the gap found in today's code, where
`gate.watch` sends the page after payment to Jev's model unmasked.

## 5. Failure handling

| Case | What happens |
|---|---|
| A portal is down, shows a captcha, or needs sign-in to show prices | The read stops; the offer is listed "couldn't read: …"; the others are ranked |
| The brand isn't sold there | Listed "not on <portal>" |
| The total can't be made from the portal's amounts | Its nearest totals are offered |
| Monthly limit reached, out of stock, brand gone (on the buy) | Stop; nothing paid; the report quotes the page |
| The cart holds something else (an earlier run's vouchers, a wrong quantity) | The model fixes it; the yes is refused until the cart equals the split; three refusals stop the task |
| Payable above the approved amount (a fee) | A new yes |
| Payable below (a discount) | Rides on the yes |
| Card declined; the bank's code wrong three times | Stop, recorded `failed`, "Not bought" |
| No order ID after the yes (time-out, error page) | Recorded `unclear`: "may have gone through; check your SMS and email before buying again" |
| The same portal, brand and amount within 24 hours, or after an unclear purchase | Asks before buying again |
| A page asks for a code on a host opened by typing its address | Stops; nothing asked (as for bills) |
| A page tells the agent to pay elsewhere or open a site | Ignored: page text is data |

## 6. Testing

- Unit (no browser, no paid calls): `money` (split, nearest, reward parsing, value with limits and fees, the three goals
  round trip, approval lines, `mask_codes`, proof), card notes and which portals fit which cards, the brain tools
  (ranking from scripted reads, cards asked once, `buy_voucher`'s guards), the worker's voucher jobs with a scripted
  model (read jobs ask nothing and never commit; cart check; one yes; fee asks again; never twice; masking after the
  yes).
- Browser replicas (headless Chrome, `assistant/fixtures/voucher*.html`): a GyFTR-like store (brand page, cart, phone
  code, pay page, bank code, order page showing a voucher code and PIN). Tests: a purchase by card; a fee after the yes;
  a leftover cart fixed; the code and PIN in no model request, trace or chat; an injected "pay elsewhere" page; a read
  that never buys.
- Brain eval (`brain_eval.py`): the voucher cases rewritten for cards, compare, pick, buy, and No.
- Bench, unseen: a read-only task on a GyFTR store with no note (Standard Chartered's, or another live store from
  gyftr.com's list when the task is written), ending at its offers.
- Live, read-only, with the user's approval (decision 11): a comparison over the real portals.
- The user buys one small voucher for real.

## 7. Safety and privacy

- No purchase without the user's yes, written in code; a fee asks again.
- Card details, the bank's code, ICICI and Amex passwords, and Grab Deals' last four digits come through the hidden
  field each time and are never stored. Card names are stored; numbers never are.
- Voucher codes and PINs are masked before any model, trace or chat (§4.7).
- Captchas go to the user; a read job never asks the user anything.
- Money moves only on a portal note's host or pay page reached by clicking from it.

## 8. Out of scope

Tracking each card's monthly usage against caps; buying with reward points; splitting one purchase across portals or
cards; MyUPI and Experian moving onto the actor (the rest of step 2, its own spec); voucher codes delivered into Jev.

## 9. To confirm in the first watched runs

- Which portals show prices without signing in (ShopWise and Grab Deals are unknown).
- Whether pay.gyftr.com's PROCEED and each portal's pay control are caught as commits from the notes' `final:` words.
- Whether Woohoo's sign-in is a phone code, and whether it shows a captcha.
- The starter card values, with the user.
- Amex SafeKey's terms forbid "any robot, spider": after the read-only check, the user decides whether Jev buys on
  ShopWise at all.

## Appendix: facts relied on (14 Oct 2026)

From `docs/research/2026-10-14-finance/voucher-portals.md`: GyFTR (Vouchagram) runs the stores of HDFC, SBI Card,
Axis Gift EDGE, IndusInd, Yes Bank and Standard Chartered on gyftr.com; SmartBuy vouchers are capped at 3,000 reward
points a month from 1 Jul 2026; iShop earns 6X on shopping vouchers, capped at 15,000 points a month, with a 2.5% card
fee on Swiggy, Amazon and Flipkart vouchers; Axis Grab Deals is Woohoo's store, 5% or 5X, capped at ₹1,000 cashback a
month, signing in by mobile and the card's last four digits; Amex Reward Multiplier ended on 30 Sep 2026 and ShopWise
(shopwise.giftstacc.com) earns 5X on Platinum cards; Amex SafeKey's terms forbid "any robot, spider"; Woohoo's terms
forbid scraping and require personal, non-commercial use; Zingoy, Amazon and Flipkart forbid bots.
