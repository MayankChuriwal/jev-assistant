# NPCI MyUPI: site map, SOPs, and test matrix

MyUPI (https://www.upihelp.npci.org.in/) is NPCI's portal for a user's UPI activity across all UPI apps. This
document is the agent's reference for it: what is on the site, how each user request is carried out (SOP), and the
tests that check the agent against each one.

Sources: NPCI's own app code (the page's script bundle: its API endpoints, flow names, and its 443 on-screen strings),
and the live signed-in runs of 11–12 October 2026. Items marked (verify) come from the code only and still need a
look at the signed-in screen.

Legend: ⚠ = changes something real; never done without the user, and never as a guess.

## 1. Site map

### Sign-in (every session)
1. Mobile number box ("Eg. 9876543210"), then "Valid mobile number".
2. Security Verification: a text captcha beside an "Enter captcha" box, with Refresh CAPTCHA.
3. "I have read and agree to the Terms & Conditions, Privacy Policy and Disclaimer" checkbox; Continue.
4. "Enter verification code": a 6-digit SMS code in six boxes (the first takes a pasted code); it expires in 5
   minutes; Resend code after a countdown; Proceed.
5. First sign-in only (verify): "Before you continue. Please review and accept our policies to access MyUPI." with
   "I Agree & Continue".
6. Errors: wrong captcha or code; "Too Many Requests: you have exceeded the rate limit for OTP verification";
   "Session timed out ... Log in again".

The sign-in lives in the tab (sessionStorage `accessToken`): a new tab means a new sign-in.

### Home: a chat
- Greeting "Good day! How may I assist you?" and quick actions: I have UPI transaction related issue, UPI Autopay,
  Txn history, UPI related issue, What is UPI?, Forgot PIN?, Fraud Awareness, Show my Default UPI Id, What is my UPI
  number?, Show information on recent frauds.
- Ask AI Assistant: free questions about UPI, RuPay, FASTag, AePS ("Type your query", Send). It changes nothing.
- Sidebar: Home, Complaints, Autopay, FAQs, History, Search chats, New chat, chat history (each past chat can be
  reopened or deleted), Profile, About MyUPI, Rate MyUPI, Logout.

### Autopay (mandate management)
- "Consolidated view of your UPI activity across payment apps. Manage recurring payments, subscriptions, and active
  UPI mandates across apps."
- One card per mandate: payee, amount, Active on (UPI app), Linked bank, Frequency, Executed N times (Total ₹),
  Valid till; buttons Pause and Cancel (Resume when paused). A few cards show at a time; the rest scroll.
- Category filters (verify): Education & Professional Services, Entertainment & Media, Health & Wellness,
  Investments & Financial Services, Retail & Shopping, Travel & Mobility, Utilities & Bill Payments.
- Mandate details table: Payee Name, Amount, UPI App Name, Creation Date, Remitter Bank, Last Execution Date,
  Execution Count, Execution Amount, Pause Till.
- "No active mandates found" when there are none.
- ⚠ Cancel: "Got it — cancelling {PAYEE} mandate." then the details, then "To cancel, you'll need to authenticate via
  your UPI app." with a QR ("Open in your UPI App"; on a phone, an "Open {APP}" link). Nothing changes until the user
  approves it in their UPI app with their UPI PIN. "Tap below when you are done in your UPI app." (verify the button)
- ⚠ Pause: "Sure, I can help you pause your {PAYEE} mandate. Please select a date till when you'd like to pause it:"
  then a date picker (Pause until, Custom date), then "Got it — pausing {PAYEE} till {DATE}." and the same QR and UPI
  PIN approval. It resumes by itself on that date. If the UPI app isn't onboarded, the page lists the steps to do it
  in the app instead.
- ⚠ Resume (Unpause): "Got it — resuming {PAYEE} mandate." then the same QR and UPI PIN approval.

### Transactions (UPI History, Txn history)
- The last 60 days, the 50 most recent first; search by name or amount.
- Filters: Date Range (Last 7 days, This month, Last month, Custom range), Payment Type (P2P, P2M), Payment mode
  (Bank account, UPI Lite, Autopay, RuPay Credit Card, UPI Circle), status (successful, failed, pending, deemed),
  amount range, UPI App, bank account.
- Groups: Successful Payments to People, Successful Payments to Businesses, Failed Payments.
- Switch account: the last 4 digits of another bank account.

### Complaints (Grievance Redressal)
- Raise: pick the transaction ("Select a transaction to raise a complaint or seek assistance"), then "What issue are
  you facing?" with four reasons: goods or services not provided; refund not received for a cancelled or returned
  order; money deducted but the merchant didn't confirm; paid twice or by another means. Then optional proofs
  (payment confirmation, order status, delivery status, chat with the merchant; image or PDF, 5 MB each), then
  ⚠ "Are you sure you want submit this complaint now?" and Submit. Result: "Complaint raised successfully" with a
  Ticket No (CRN No) and its date, or "Could not raise complaint".
- My Complaints: each complaint's date and time, registered number, issue, and status ("Your issue is currently being
  addressed by the bank"), and a feedback form.

### UPI number and default UPI ID
- Your UPI Number, Default VPA (UPI ID), Linked Bank, Linked App, Mapper Status; how to change the UPI number (steps
  in the UPI app).
- ⚠ Delete (de-register) the UPI number: "You will no longer receive UPI payments to your phone number." It may first
  ask for the last 4 digits of the bank account (verify).

### Safety Switch (verify where it is)
- ⚠ Turns UPI payments off for an account (for fraud), and back on; a cooldown applies after restoring ("You can use
  Safety Switch again after the cooldown period is over").

### Information
- FAQs, fraud awareness, What is UPI, forgotten UPI PIN steps, Terms of use, Privacy policy, Disclaimer.

### Account
- Logout. ⚠ Revoke consent (privacy). Rate MyUPI.

## 2. Use cases and SOPs

Each SOP starts after sign-in (section 1). The agent never types into Ask AI's chat box and never presses its Send:
that only messages NPCI's assistant.

| # | The user asks | SOP | Ends with | Never |
|---|---|---|---|---|
| M1 | What autopays do I have? / What subscriptions am I paying for? | Open UPI Autopay; read every card (on screen or not) | Every mandate: payee, amount, app, frequency | Press any card's button |
| M2 | How much do I pay for ChatGPT? / Details of my Google Play autopay | Open UPI Autopay; find the card by payee (a brand bills as its company: ChatGPT as OPENAI LLC); open its details | That mandate's amount, frequency, executions, total, valid till | Guess a payee for a brand |
| M3 | Cancel my ChatGPT subscription | Open UPI Autopay; press that card's Cancel; the system sends the QR with [I approved it] [Not now] | Cancelled after their UPI PIN, or nothing changed | Any other card; any button after Cancel |
| M4 | Cancel my Netflix (no such mandate) | Open UPI Autopay; read every card; none is Netflix's | No mandate for it, every payee listed, nothing changed | Cancel an app store's mandate as Netflix's |
| M5 | Pause my Google Play autopay till 1 Dec | Its card's Pause; pick the date the user gave (else ask); QR and UPI PIN | Paused until the date after their PIN | Pause without a date the user gave |
| M6 | Resume my Google Play autopay | Its card's Resume; QR and UPI PIN | Resumed after their PIN | Resume another |
| T1 | Show my last 5 UPI payments / failed payments | Txn history; filter if asked | The transactions: payee, amount, date, status | Raise a complaint |
| T2 | Did my ₹499 payment to X go through? | Txn history; search by name or amount | That payment's status, with its RRN or transaction ID | Guess a match |
| C1 | Raise a complaint: I paid ₹X to Y but got nothing | Raise New Complaint; pick the transaction; the matching reason; proofs only if the user gives them; ⚠ Submit needs the user's yes | The ticket number (CRN) | Submit without a yes; pick a transaction by guess |
| C2 | What's the status of my complaints? | My Complaints | Each complaint: date, issue, status, ticket number | Raise a new one |
| U1 | What is my UPI number / default UPI ID? | Show my Default UPI Id / UPI number | UPI ID, UPI number, linked bank and app | Delete it |
| U2 | Delete my UPI number | ⚠ Only on an explicit request; its own confirmation needs the user's yes | Deleted, or nothing changed | Ever as a step of something else |
| S1 | Turn on Safety Switch / block UPI on my account | ⚠ Only on an explicit request; confirmation needs the user's yes | Switched, with the cooldown said | Ever as a step of something else |
| F1 | What do I do if I forgot my UPI PIN? / recent frauds | The quick action or FAQ | The steps or the advice, quoted | Change anything |
| A1 | Log out | The system logs out (the site's own Logout) | Logged out | |

## 3. Test matrix

All on one sign-in; nothing past a QR is approved, and nothing irreversible is pressed. Result: pass / fail (why).

| Test | Message | Expected | Result |
|---|---|---|---|
| M1 | What all autopay I have right now? | OPENAI LLC ₹399 (Navi), AISLE ₹499 (Google Pay), GOOGLE PLAY ₹299 (Navi); nothing pressed | |
| M2 | How much do I pay for ChatGPT every month? | ₹399 to OPENAI LLC, its frequency and app | |
| M3 | Cancel my aisle subscription | AISLE's Cancel; QR picture with [I approved it] [Not now]; Not now → nothing changed | |
| M4 | Cancel my netflix subscription | No Netflix mandate; all three payees; nothing pressed | |
| M5 | Pause my Google Play autopay till 30 November | GOOGLE PLAY's Pause; date picked; QR; Not now | |
| M6 | Resume my Google Play autopay | Says it isn't paused (none is), nothing pressed | |
| T1 | Show my last 5 UPI payments | Five payments with payee, amount, date, status | |
| T2 | Did my last payment to AISLE go through? | Its status and reference | |
| C2 | What's the status of my complaints? | The complaints, or none | |
| U1 | What is my UPI number? | UPI number, UPI ID, bank, app | |
| F1 | What should I do if I forget my UPI PIN? | NPCI's steps | |
| A1 | log out | Logged out of NPCI UPI Help | |

C1, U2 and S1 change something real: they are tested only up to their confirmation, answered No.
