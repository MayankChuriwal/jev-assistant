---
name: forms
description: Fill in and submit a form on a website (orders, registrations, complaints, applications)
inputs:
uses: sign-in
summary: every value the form will send, the form's name
report: the confirmation or reference shown after submitting
---
## Plan
1. Open the form: the address the user gave, or the site's form page.
2. Fill every field the user gave a value for, in order; profile details as [profile:name], [profile:phone], [profile:email].
3. For required fields the user gave no value for, ASK once for all of them.
4. Check that each field shows its value (dates and times in the field's own format).
5. Submit: the final control is the commit. Then report the confirmation.
## Rules
- Leave optional fields empty unless the user gave a value.
- Choices in the form (sizes, toppings, radio buttons, dropdowns) follow the user's words, never guesses.
