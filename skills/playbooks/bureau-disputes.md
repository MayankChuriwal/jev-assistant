---
name: Credit report disputes
when: something wrong on the user's credit report: an account, their personal details, or an enquiry they didn't make
---
- bureau_dispute raises a dispute on the user's credit report with a bureau. Experian is the bureau it covers today.
- A dispute needs its kind (Account Details: an account's status, balance, limit or dates, or an account that isn't theirs; Personal Details; Credit Enquiry), the account's lender and last 4 digits for an account, what the report shows, and what it should show.
- Until you have those details, call no tool: the dispute's steps are known, so never open the bureau's site to learn them. Ask only for what's missing, one short question at a time, with options when the answer can be listed. Then call bureau_dispute right away: the bureau's final submit asks for the user's yes itself, so don't ask again.
- The tool types the Experian Reference Number (ERN) and Unique Transaction ID (UTI) from the user's saved IDs, or asks for them in a hidden field. They come from an Experian report pulled in the last 30 days. Never ask for them in chat.
- If Experian can't verify them (too old, a mismatch, or "Error while verifying"), say so: both must come from one report pulled in the last 30 days. To retry with new ones, call bureau_dispute with fresh_ids true: it forgets the saved pair first, so the hidden field asks for both.
- Never fill a detail with placeholder words ("not yet provided"): ask the user for it.
- Report what was disputed and any reference Experian shows; Experian emails an acknowledgement.
