# Jev — Buying a gift voucher on HDFC SmartBuy and ICICI iShop (demo)

Date: 2026-10-13. Branch: `mini-jev-agent`. Builds on `2026-10-12-brain-and-hands-design.md`; everything it specifies
stays unless this spec changes it. Demo target: a VC demo where the user says "Buy me a Swiggy voucher" in the Rivo
phone page and Jev does the job on HDFC SmartBuy or ICICI iShop, up to the point of paying.

## 1. Why

The MyUPI cancel and the Experian dispute show Jev acting for the user on a real site. Buying a gift voucher on a
bank's rewards portal is the most Rivo-shaped task yet (the right card, its reward rate, the portal the bank pays out
on). The brain + hands architecture was built so a use case adds tools, a playbook and site notes, not new routing.

## 2. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Money | No money moves. Jev goes up to its approval card for the portal's final pay control; the user taps **No** on camera. No card number, CVV or bank OTP is ever entered |
| 2 | Portals | Both: HDFC SmartBuy (Instant vouchers) and ICICI iShop. The user holds a card and an account on each |
| 3 | Amount | Read first: Jev signs in once, reads the brand's real amounts and reward rate off the portal, and asks "Which amount?" with them as buttons |
| 4 | Portal not named | Jev asks "SmartBuy (HDFC) or iShop (ICICI)?" with buttons |
| 5 | Approach | A: two brain tools (`voucher_options`, `voucher_checkout`) with code guards, a playbook, two site notes, a `final:` site-note flag, and a plain No that ends the task. Step-by-step code only where supervised runs show the planner fumbling |
| 6 | Learning the portals | I map each portal's public pages read-only in Jev's own Chrome, then one supervised signed-in run per portal: the user types the OTP in the hidden field and I fix the notes from the live trace |
| 7 | Spec and plan | This spec, then a plan, per the brainstorming path |

## 3. The conversation

1. User: "Buy me a Swiggy voucher". Jev: "SmartBuy (HDFC) or iShop (ICICI)?" with two buttons. A request that names the
   portal skips this; one that names no brand gets "Which brand?".
2. The user taps SmartBuy. The brain calls `voucher_options(SmartBuy, Swiggy)`; the status line reads "Reading Swiggy
   vouchers on SmartBuy…". The worker signs in (the phone from the user's profile; the OTP in the hidden field; any
   captcha as a picture), opens Swiggy's voucher page, and reports every amount with its price and the reward rate.
3. Jev: "Swiggy on SmartBuy: ₹250 · ₹500 · ₹1,000 · ₹2,000, 5X reward points. Which amount?" with the amounts as buttons.
4. The user taps ₹500. The brain calls `voucher_checkout(SmartBuy, Swiggy, 500, 1)`. The worker continues on the same
   signed-in page (no second OTP), picks ₹500, quantity 1, goes to checkout, and reaches the final pay control. Jev's
   approval card: "Swiggy e-voucher ₹500 × 1 · Pay ₹500 · 165 points · sent to your email. I'll press “Proceed to Pay”
   on SmartBuy." with Yes and No.
5. The user taps No. Jev: "Stopped before paying, as you chose. Nothing was charged."

## 4. The brain (`assistant/brain.py`)

- `PORTALS = {"SmartBuy": <host>, "iShop": <host>}`: each portal's site note, like `BUREAUS`. Hosts come from
  exploration (§7).
- `voucher_options(portal, brand)`: portal is an enum of `PORTALS`. Rejected, with a `next` hint, when the brand isn't
  in the user's own recent words ("Ask which brand, with options"). Runs a read task: "Show the {brand} gift vouchers
  {portal} sells: every amount with its price, and the reward rate shown. Buy nothing." with the status line above. A
  done read is kept per (portal, brand); a second call returns it as `already_read` with "don't read again" (the
  Experian lockouts came from re-reads). The ₹ amounts in the read are kept too, parsed in code.
- `voucher_checkout(portal, brand, amount, quantity)`: `amount` an integer in rupees, `quantity` an integer (1 unless
  the user asked for more). Rejected when no read exists for (portal, brand) ("Read the amounts first with
  voucher_options") or when `amount` isn't one of the read amounts ("Offer the amounts it sells"). Runs a task: "Buy a
  ₹{amount} {brand} gift voucher, quantity {quantity}, on {portal}." on the kept page. An outcome `not_approved` returns
  `next`: "Say you stopped before paying and nothing was charged. Call no other tool."
- Playbook `skills/playbooks/vouchers.md`: when the user wants a gift voucher, e-voucher or gift card from a bank's
  rewards portal. Ask the portal when it isn't named, then the brand when it isn't; read the amounts with
  `voucher_options` and ask which, with the real amounts as options; then `voucher_checkout`. Its final step asks the
  user itself: never ask again. A No means it stopped and nothing was charged. Never ask for card details.

## 5. The hands

### 5.1 Site notes (`skills/sites/`)

One note per portal, written from exploration. Frontmatter: `host`, `name`, `aliases` ("SmartBuy, HDFC SmartBuy,
Instant vouchers" / "iShop, ICICI iShop"), `start` (the voucher section), `learn: no`, `keep: page` (or `tab` when the
sign-in survives a reload), `sign-in: auto` when the sign-in is a phone-and-OTP form, `close:` for any notice that
blocks the page, and the new `final:`. Body: where vouchers live, how to find a brand, the brand page's amount and
quantity controls, what checkout shows (amount, points, where the voucher is sent), never entering card details, and
which control is final.

### 5.2 The `final:` flag (`assistant/gate.py`)

`final: Proceed to Pay` names the control that leaves the portal for payment. In `gate.commit_label`, a control on a
page whose site note has `final:` and whose label starts with one of its words (case-insensitive; several may be
listed, separated by commas) is a commit outright: Jev's check that clears a "step to the payment page" is never asked.
Today "Proceed to Pay" is a maybe-commit that Jev may clear (`gate.py` §commit_label), which would open a pending order
and the bank's card form before any approval.

### 5.3 A plain No ends a purchase (`assistant/gate.py`)

Today a declined approval raises "The user did not approve: … do what they asked", and the planner continues. On every
site, a plain No (the No button, or a reply whose words are all no-words: no, nope, nahi, stop, cancel, don't) ends the
task: outcome `not_approved`, result "You chose not to pay. Nothing was charged." Any other reply ("No, make it ₹1,000")
keeps today's behavior, so the user can still redirect. The brain's `voucher_checkout` maps `not_approved` to §4's
`next`.

### 5.4 The approval card

The existing summary path reads the page: brand, amount × quantity, the payable total, points, and where the voucher
goes. Voucher tasks run with the existing `shopping` skill, whose summary fields cover these. The filler-line filter
from 12 Oct stays.

### 5.5 Step-by-step code

None at first. Added only where a supervised run shows the planner fumbling, as `row_step` and `notice_step` were for
Experian: each one keyed by a site-note flag, tested first, and named in the note.

## 6. The demo screen (`assistant/static/index.html`)

The "My UPI ID" starter card becomes "Buy a voucher" with "Swiggy on SmartBuy or iShop", and sends "Buy me a Swiggy
voucher".

## 7. Exploration and verification

1. Public pages, read-only, in Jev's own Chrome (port 9333), never the user's everyday Chrome: each portal's voucher
   section, Swiggy's voucher page, how amounts, quantity and Buy are laid out, and where sign-in starts. Never signing
   in, typing anything personal, or pressing Buy. Output: hosts, start URLs, draft notes.
2. One supervised signed-in run per portal: the user types the demo prompt and the OTP in the hidden field; I watch the
   live trace and fix the note (or add step-by-step code, §5.5); the run must reach the approval card on the portal's
   checkout, and the user's No must end with "Nothing was charged".

## 8. Testing

- Unit tests first (TDD): `voucher_options` and `voucher_checkout` guards (portal, named brand, amount from the read,
  no re-read, `not_approved` mapped), the `final:` flag (a matching control asks without Jev's check; a non-matching
  one keeps today's rules), a plain No ending a purchase and another reply redirecting it, the playbook loading.
- Brain evals with the real model, three new cases: "Buy me a Swiggy voucher" asks the portal; "from SmartBuy" calls
  `voucher_options`; a tapped amount calls `voucher_checkout` with that read amount.
- The unit and browser suites pass before every commit.

## 9. Safety and privacy

- No card details are entered: the demo stops at the approval card, and the hidden-field payment path is unchanged.
- One sign-in per flow (`keep: page`, no re-read): no lockouts.
- The user's own accounts only, low volume (as with NPCI MyUPI).
- The user's name, phone and email reach models only as `[profile:…]` placeholders; the chat shows "your phone number".
- Open from 12 Oct: Experian's account dropdown sends full account numbers to the worker's model. The user decides
  whether to mask them now; any card or account number these portals show is covered by the same decision.

## 10. Out of scope

Real payment; voucher codes or PINs (the portal emails or texts them); comparing rewards across portals; brands beyond
what the portals sell (the tools take any brand the user names).
