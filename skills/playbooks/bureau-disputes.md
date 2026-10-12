---
name: Credit report disputes
when: something wrong on the user's credit report: an account, their personal details, or an enquiry they didn't make
---
- bureau_dispute raises a dispute on the user's credit report with a bureau. Experian is the bureau it covers today.
- A dispute needs its kind (Account Details: an account's status, balance, limit or dates, or an account that isn't theirs; Personal Details; Credit Enquiry), the account's lender and last 4 digits for an account, what the report shows, and what it should show.
- First ask which kind (an account, personal details, or a credit enquiry), with options. Then read what the report holds for it with bureau_details (it signs in: the ERN, UTI and Experian's code go in hidden fields), and ask which entry is wrong with its real values as options ("Address 2: 4 Park Street"), then what it should show. Never ask for a new value before the user has picked the entry it replaces.
- Then call bureau_dispute naming that entry exactly (wrong: "Address 2: 4 Park Street"); the bureau's final submit asks for the user's yes itself, so don't ask again. Never open the bureau's site any other way to learn its steps.
- The tool types the Experian Reference Number (ERN) and Unique Transaction ID (UTI) from the user's saved IDs, or asks for them in a hidden field. They come from an Experian report pulled in the last 30 days. Never ask for them in chat.
- If Experian can't verify them (too old, a mismatch, or "Error while verifying"), say so: both must come from one report pulled in the last 30 days. To retry with new ones, call bureau_dispute with fresh_ids true: it forgets the saved pair first, so the hidden field asks for both.
- Never fill a detail with placeholder words ("not yet provided"): ask the user for it.
- Report what was disputed and any reference Experian shows; Experian emails an acknowledgement.
