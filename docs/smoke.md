# Candidate live smoke set

This set supports the [approved local reliability design](superpowers/specs/2026-09-24-local-agent-reliability-design.md).
It is not a record of successful runs. Live evaluation has not been authorized or run for Task 14.
See [reliability-evaluation.md](reliability-evaluation.md) for declarations, records and remaining gates.

## Before an authorized evaluation

1. Agree the exact natural-language goals and observable outcomes in chat. Supply missing accounts,
   addresses, pickup/destination and two unrelated holdout goals; none are invented by the developer.
2. Install with `uv sync`; put real keys only in ignored `.env`. `uv run jev probe` is an optional availability
   diagnostic, not proof of login or task feasibility.
3. Start `uv run jev` and use local chat at `http://127.0.0.1:8766/`. Only the dedicated agent Chrome
   (`BU_NAME=jev`, port 9333) may be used. No manual completion in a website or site-specific scripts.
4. Record fresh login versus session reuse. Supply OTPs, CAPTCHA answers and temporary payment secrets
   in chat. Never save a card/CVV or operate a native prompt/banking app to make a run pass.
5. Give each external action its own concrete final approval after the complete fee/material review.
   A decline is a cancellation/preparation result, never a successful purchase or booking.

## Candidate goals and success conditions

| ID | Natural-language candidate | Class | Required observable result |
|---|---|---|---|
| S1 | Order one Diet Coke 300 ml on Swiggy Instamart at [address] | Real purchase; fresh login | Correct item/quantity/address and confirmed new order/reference with actual total matching the approved review |
| S2 | Book an Uber from [pickup] to [destination], with a confirmed driver | Real booking | Correct route/class, confirmed driver/car/plate and agreed fare evidence; finding drivers is not success |
| S3 | Order [dish] from [restaurant] on Swiggy | Real purchase; session reuse where available | Correct dish/options/address and confirmed new order/reference/total |
| S4 | Order [item/variant/quantity] on Amazon.in | Real purchase | Correct listing/variant/quantity/destination and new order/reference/charge evidence |
| S5 | Submit a complaint about [issue] using [official organisation form] | External submission | Correct supplied details/recipient and a new submission reference |
| S6 | Download my latest invoice from [provider/account] and send it here | File retrieval | Requested invoice identity/period/account and actual document delivered in chat, linked to the execution and requirement |
| S7 | Compare [item] prices, including all fees, and let me choose providers | Preparation/comparison; provider omitted | Provider/comparison choice asked first, explicit consent, separate login/OTP notice, evidence-backed comparable quotes; no purchase |
| S8 | Resume [blocked task] after [supported correction] | Recovery | Fresh observation, retained valid answers and intended remaining result without duplicate mutation; unknown commitments reconciled read-only |
| H1 | User-selected unrelated holdout: pending | User to classify | User must declare an observable success condition before execution |
| H2 | User-selected unrelated holdout: pending | User to classify | User must declare an observable success condition before execution |

The exact providers, variants, addresses, accounts and holdouts are pending user input. This is a candidate
set, not a finalized acceptance sample. No success rate is calculated before these are agreed.
A local upload/dispute fixture can test plumbing, but is excluded from the live sample:

    python3 -m http.server 8767 --bind 127.0.0.1 --directory assistant/fixtures

Use “Open http://127.0.0.1:8767/upload.html and submit the dispute with the files I send” only as a fixture goal.
File selection does not prove server upload or final submission; each requires its own receipt/evidence.

## After every attempted run

Inspect `uv run jev traces` and the agent-owned page/receipt/artifact evidence. Record the tested SHA and
opaque trace ID, all calls/latency and questions (necessary versus duplicate), status and separate external
outcome. Preserve failed, blocked, cancelled and developer-intervened attempts; record every rerun separately.
Do not use a quote screen, Jev DONE choice or a file send alone as proof of the declared outcome.
External app-only challenges and site blocks are blockers. Product failures remain acceptance failures.
Any duplicate/unauthorized mutation, secret disclosure, false success or unnecessary repeated question
blocks acceptance until fixed and freshly evaluated.
