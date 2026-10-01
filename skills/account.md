---
name: account
description: Look after the user's own accounts: orders, order status, invoices, returns, memberships, cancellations
inputs:
sites: amazon.in, flipkart.com
uses: sign-in
summary: what will change, which order or membership, amount or refund
report: what was done or found, reference, dates, amounts
---
## Plan
1. Sign in if needed, then open the account's orders or memberships page.
2. Find the order or membership the goal names (the latest one when it says latest).
3. For a question (status, delivery date, return window): DONE with the answer.
4. For an invoice or document: open its download link; the file reaches the user by itself, even one the browser only shows; DONE naming the order once files_saved lists it.
5. For a cancellation or a return: follow the site's steps; the final control (cancel, end membership, submit return) is the commit.
## Rules
- Never cancel, return, or change anything the goal didn't name.
- A time-range filter on an orders or invoices page (last 30 days, past 3 months) is not "no orders": widen it (last year, all orders) before saying nothing exists.
- When there is no such order or membership, DONE saying so, with a quote from the page.
- A membership or subscription page that only offers plans to join ("sign up", "start your free trial", "choose a plan"), or a manage or cancel link that leads back to it, means there is no active membership: DONE with that quote, saying nothing was cancelled. Don't go round the help pages.
