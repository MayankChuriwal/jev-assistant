---
host: consumer.experian.in
name: Experian
aliases: Experian India, Experian credit report
start: https://consumer.experian.in/ECSINDIA-DCE/view/angular/index.html#!/
learn: no
keep: tab
---
- This is Experian India's page for raising a dispute on a credit report. It opens with two boxes: type [profile:experian_ern] into "Experian Reference Number" and [profile:experian_uti] into "Unique Transaction ID", then press Verify.
- The ERN is on the top right of the user's Experian report; the UTI is in Experian's email. Both must come from a report pulled in the last 30 days: if the page says they are too old, don't match, or can't be verified ("Error while verifying UTI and ERN"), STOP saying Experian couldn't verify them and a fresh Experian report is needed.
- After Verify (and the code Experian sends, which the system asks for), a goal to show the details opens the dispute type it names and ends with DONE listing every row with its current value ("Address 1: …; Address 2: …", or each account's lender, last 4 digits and fields), changing nothing.
- After Verify, pick the dispute type from the drop-down: Account Details (an account's status, balance, limit, dates, or an account that isn't the user's), Personal Details, or Credit Enquiry.
- Find the account by its lender and the last four digits the goal gives. Each detail's row has a pencil, "Edit Field (in “ROW”)" (Personal Details: Name, Date of Birth, Address 1, …): press the one in the wrong detail's row, then type the correct value the goal gives into that row's box that opens; change nothing else. The Comments box is only for a short reason, never the correction itself.
- Submit opens a pop-up listing the corrections, with an optional bank-details section: leave it empty. The pop-up's final submit files the dispute and needs the user's yes; Experian then emails an acknowledgement.
- Report the dispute's reference or acknowledgement number if the page shows one, and what was disputed.
