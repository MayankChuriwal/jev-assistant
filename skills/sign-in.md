---
name: sign-in
description: Sign in to a site with the user's phone or email and a one-time code
---
- Sign in only when the task needs an account (ordering, booking, paying, the user's own orders or invoices). Searching and reading never need it: close sign-in pop-ups instead.
- Use the site's own phone or email sign-in, never Google, Apple, or Facebook buttons.
- Type [profile:phone] (or [profile:email]) into the login field, then press the site's continue or send-code button.
- The system asks the user for the code through a hidden field and types it; if the site says the code is wrong or expired, press resend once.
- Never ask for a code the page hasn't asked for: press the button that sends one, then wait for the code field.
- A sign-in that offers only a QR code to scan (no phone, email, or password field) can't be done by the agent: ASK "Please sign in to <site> once in the agent's browser window, then say continue" and wait for the reply. Never switch back and forth between the pop-up's options.
- After a code is typed, a "passkey", "sign in faster", "save this device" or similar page means sign-in worked: choose "Not now" or "Skip". Never go back to the number or code page, and never ask for another code. A blank page right after the code: WAIT.
- A sign-in pop-up whose close (×) isn't listed: press Escape once, then LOOK for the ×; else OPEN_URL the page you wanted. Never press Escape, scroll, or click the pop-up again and again.
- After signing in, go back to where the task stopped.
