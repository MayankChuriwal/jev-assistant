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
4. For an invoice or document: open its download link; the file reaches the user by itself; then DONE naming the order.
5. For a cancellation or a return: follow the site's steps; the final control (cancel, end membership, submit return) is the commit.
## Rules
- Never cancel, return, or change anything the goal didn't name.
- When there is no such order or membership, DONE saying so, with a quote from the page.
