---
name: bills-recharges
description: Pay a bill or recharge a phone or DTH (mobile, DTH, electricity, water) on Paytm, Amazon, or the provider's site
inputs: provider, plan or amount?
sites: paytm.com, amazon.in
uses: payment, sign-in
summary: provider, account or number, plan or bill amount, payment method, total
report: transaction id, amount, provider
---
## Plan
1. Open the site's recharge or bill page for that kind of service.
2. Enter the mobile number ([profile:phone] for "my number") or the account number the user gives; choose the provider and circle when asked.
3. For a recharge: find the plan the user named (by price or benefits); ASK when several plans have that price.
4. For a bill: fetch the bill and read the amount due; the approval shows it.
5. Choose the payment method the user picks; pay: the final control is the commit. Then report.
## Rules
- Account and consumer numbers come from the user: ASK once; never guess or reuse another one.
- Give a plan's validity and data with its price.
