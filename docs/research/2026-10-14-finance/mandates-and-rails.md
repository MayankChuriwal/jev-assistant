# India recurring-payment mandates and payment rails: desk brief (14 Oct 2026)

Format: claim [tag] S# (key at end: URL + source date). "Vendor doc" = a gateway's or bank's own page: primary for its own behavior, secondary for rules it relays. No logins or submissions. Search budget ran out intermittently; npci.org.in is JS-rendered (titles only), so NPCI circular lists were unreadable.

## A. Finding and managing recurring payments

### 1. UPI AutoPay mandates
- NPCI OC-223/2025-26 (7 Oct 2025): every UPI app must show a user's active AutoPay mandates from any app and let them port a mandate to another app. View/port sit only under "Manage Bank accounts" or a "UPI Autopay" section, beside revoke/pause/modify; every payer action needs UPI PIN; porting is user-initiated, once per rolling 90 days, no nudges. Members enable by 31 Dec 2025; existing mandates keep working. [verified-primary] S1
- Status: no compliance data found. Razorpay adopts in stages from July 2026; certified payer apps Paytm, BHIM, INDmoney, not Google Pay/PhonePe [verified-primary] S2. Mint-sourced reports (31 Aug 2026) still call porting upcoming [reported-secondary] S3. Porting live in any consumer app: [unconfirmed]. RBI asked NPCI to review AutoPay complaints (Feb 2026) [reported-secondary] S4.
- UPI Help (NPCI AI assistant), 8 Oct 2025: transaction status, complaints, mandate management [verified-primary] S5. Pilot; via bank sites/chatbots and DigiSaathi; deep-links into the UPI app to finish pause/resume/revoke [reported-secondary] S6. MyUPI (UPI Help 2.0), 10 Sep 2026: "single view of UPI transactions and AutoPay mandates across banks and UPI apps" [verified-primary] S7.
- Web: https://upihelp.npci.org.in exists (title "MyUPI | Raise UPI Complaints...") [verified-primary] S8. Guides (Dec 2025 to Jan 2026): registered mobile + OTP login, "My Mandates" tab, Revoke button, UPI PIN for actions [reported-secondary] S9, S10. CAPTCHA: Codeforbanks (2 Jan 2026, search excerpt) says yes, TrackAutoPay silent [unconfirmed] S9. Bank-site UPI-mandate manager: none found [unconfirmed].

### 2. Card standing instructions on the web
- RBI E-mandate Framework 2026 (21 Apr 2026; cards, PPI, UPI; repeals eight 2019-24 circulars): customer may change validity or withdraw (4(b)) with AFA (4(e)); opt out of one debit or the mandate (6(c)); no fees. Viewing unaddressed. [verified-primary] S11
- Hubs: BillDesk SI-Hub (2019); Razorpay MandateHQ (bank-branded) [verified-primary] S12, S13.
- ICICI: https://www.sihub.in/managesi/icici, or https://icici.mandatehq.com/login if Mandate ID starts "IC"; card number; view/modify, pause/opt-out; says it cannot cancel for you (page 2026) [verified-primary] S14
- Axis: https://www.sihub.in/managesi/axisbank (email sign-up, add card, ACS auth) or https://axis.mandatehq.com (card number + OTP); view/modify/cancel [verified-primary] S15
- Federal: https://www.sihub.in/managesi/federal/#/home/landing (card number + OTP; one-time email sign-up); edit end date/amount, cancel (page 14 Oct 2026) [verified-primary] S16
- HSBC: https://hsbc.mandatehq.com (name/mobile/email sign-up, register card, OTP); view, pause, resume, cancel, approve debits above INR 5,000 [verified-primary] S17
- HDFC: hdfcbank.com login > "Merchant Standing Instruction"; https://www.sihub.in/managesi/hdfcbank [verified-primary] S18. Unified MandateHQ portal (view/pause/resume/cancel/approve) live 8 Sep 2026, URL not found [verified-primary] S19
- SBI debit: onlinesbi.sbi pre-login or https://www.sihub.in/managesi/sbi, card number + OTP (page 2 Mar 2024) [verified-primary] S20. SBI Card: sbicard.com/emandates after login [verified-primary] S21. IDFC FIRST: https://www.sihub.in/managesi/idfcfirstbank, OTP (2021 doc) [verified-primary] S22
- Issuer pages disagree on limits (Federal, SBI Card, HSBC, IDFC cite INR 5,000; HDFC, Axis, ICICI cite 15,000/1 lakh) [verified-primary] S11, S14-S22

### 3. e-NACH/NACH
- Net/mobile banking > Service Request/Mandates > select > OTP. Capital SFB says NPCI obliged banks to offer online cancellation (guide Apr 2026) [verified-primary] S23. Punjab & Sind: cancel/amend/suspend/revoke via net banking, debit card, Aadhaar [verified-primary] S24. IndusInd e-NACH page: "View/Cancel Existing Mandate(s)" + OTP [verified-primary] S25. No direct NPCI consumer channel described [reported-secondary] S26. NPCI rules per a LotusPay doc: customer cancels online via destination bank, SMS OTP (email acceptable); circulars 2020-21 No. 13/24, 2021-22 No. 25/28 [reported-secondary] S32.
- Lenders resist: FIDC to RBI (25 May 2026) says banks cancel on customer instruction alone since RBI withdrew its 18 Apr 2012 circular, cites NPCI/2025-26/NACH/001 (Apr 2025) making sponsor banks accountable for improper termination, and seeks lender confirmation for loan-linked mandates; outcome not found [verified-primary for letter; circular content reported-secondary] S27
- NPCI/2026-27/NACH/001 (28 Apr 2026) only revised the physical mandate form [reported-secondary] S28. RBI's 2026 framework excludes NACH [verified-primary] S11. NPCI circular texts unread [unconfirmed].

### 4. Rules
- No AFA up to INR 15,000 per recurring txn; INR 1,00,000 for insurance premiums, mutual fund subscriptions, credit-card bills (para 8); first txn needs AFA (5(a)); mandate payments ignore customer-set limits (5(b)) [verified-primary] S11. On UPI, above 15,000 needs UPI PIN approval; MCCs 6211/6300/7322/6529/5960 to INR 1 lakh [reported-secondary] S29. Juspay's "PIN above 2,000" is stale [reported-secondary] S30.
- Pre-debit notice at least 24 h before: merchant, amount, date/time, mandate ref, reason; customer picks SMS/email; opt-out needs AFA; FASTag/NCMC exempt [verified-primary] S11. UPI: merchant triggers 48-24 h before; bank/PSP notifies; debit created ~25 h later [reported-secondary] S29, S30.
- Pause: customer-only; API takes pause start/end timestamps; effect on debits and auto-resume undocumented; irrevocable lending mandates (MCC 7322) lack in-app pause/cancel; modify = cancel and recreate (Razorpay) [reported-secondary] S29, S31.
- Expiry: each e-mandate states a validity; customer can change/withdraw (4(b)) [verified-primary] S11. NPCI maximum tenure [unconfirmed].

## B. Rails for a desktop browser agent

### 5. UPI collect
- P2P: NPCI/UPI/OC/220/2025-26 (29 Jul 2025): "by October 1, 2025 UPI P2P collect shall not be allowed"; prior cap INR 2,000/txn, 50/day; merchant collect continued then [reported-secondary] S33, S34.
- P2M "enter UPI ID": Razorpay, PayU, Cashfree say NPCI-mandated deprecation from 28 Feb 2026 (Juspay: 31 Jan); no typing VPA/UPI ID/mobile to pay or register mandates; desktop gets QR, Android Intent [verified-primary, vendor behavior] S35-S38. NPCI circular not found [unconfirmed].
- Exempt: MCC 6012/6211, iOS app and mobile web (until further notice), UPI mandate execute/modify/revoke, eRupi, PACB; Cashfree: all Indian gateways [verified-primary] S35-S37.
- BillDesk collect doc (Nov 2025) has no deprecation note; PhonePe PG: nothing found; P2M collect cap: none found [unconfirmed] S39.
- NPCI (13 Oct 2026) deferred MDR on select P2M UPI to just after Diwali; reported 0.4% above INR 2,000, merchants pay [verified-primary] S40; [reported-secondary] S41.

### 6. Dynamic QR and upi:// links
- Format (NPCI Linking Specs v1.6 draft, Nov 2017): upi://pay?pa=&pn=&mc=&tid=&tr=&tn=&am=&mam=&cu=INR&url=&mode=&orgid=&sign=; pa, pn mandatory; tr for merchant/dynamic; am for dynamic; mode 01 QR, 02 secure QR, 04 intent, 05 secure intent [verified-primary, older] S42
- Expiry: none in spec; Razorpay QR close_by 2 min-2 h, default 2 h (page also says 15 min); Juspay: QR never expires, merchant timer [verified-primary] S43, S44. Gateways build desktop QR from the intent URL [verified-primary] S35, S36.
- Android: PSP apps must handle upi:// (spec); PhonePe/GPay mobile-web intent works only in Chrome [verified-primary] S42, S45.
- iOS: generic upi:// "by default" opens WhatsApp payments (Juspay); gateways use per-app schemes (gpay://upi/pay, phonepe://pay, paytmmp://pay) [verified-primary] S45-S48.
- Chat: spec lists "share via chat; recipient clicks" as intended, but Cashfree (14 Oct 2026) says a raw upi:// link in WhatsApp "typically appears as a flat URL preview" [reported-secondary] S42, S67. WhatsApp Business `UPI_INTENT` order messages open the default UPI app on tap; Meta "Enhanced Payment Links" switch to GPay/PhonePe/Paytm but are "not publicly available yet" [verified-primary] S68. A tappable bare upi:// on Android/iOS: [unconfirmed].
- NPCI Banking Connect (8 Oct 2025): net banking via QR/Intent, mobile-first [verified-primary] S5.

### 7. Agentic payments
- NPCI-Razorpay-OpenAI (9 Oct 2025): agentic payments on ChatGPT via UPI Reserve Pay + UPI Circle; Axis, Airtel Payments Bank; BigBasket; pilot stage [verified-primary] S49; Razorpay said Gemini/Claude would go live "in a few weeks" [reported-secondary] S50. Razorpay + NPCI on Claude (20 Feb 2026): Zomato, Swiggy, Zepto; per-merchant limit, no repeat PIN; small pilot [verified-primary] S51. Reserve Pay "early access" (12 Mar 2026) S52; page: Reserve Pay live, Circle "coming soon" [verified-primary] S53.
- Reserve Pay (SBMD): NPCI says users block part of a credit limit (8 Oct 2025) [verified-primary] S5. Pine Labs FAQ: INR 10,000/block, 90 days, one active mandate, Paytm/Navi/BHIM live, no in-app revoke (Razorpay disagrees) [reported-secondary] S54. UPI Circle: INR 5,000/txn, 15,000/month per delegate (2024) [reported-secondary] S55.
- NPCI: no agent framework yet; chairman (10 Sep 2026): AI "may recommend", authentication/settlement deterministic [reported-secondary] S56. Unified Agent Protocol + registry reported (Business Standard 9 Jul; Reuters 1 Sep 2026), NPCI unconfirmed [reported-secondary] S57, S58. Open registration for any developer: unanswered (MediaNama asked NPCI); only Razorpay early-access forms found [reported-secondary] S56, S52.
- Mastercard Agent Pay: first authenticated India agentic txn (17 Feb 2026), controlled demo, Axis/RBL cards [reported-secondary] S59. Visa Intelligent Commerce: GFF demos 10 Sep 2026; India launch only "planned" [reported-secondary] S60.

### 8. Card authentication
- RBI Authentication Directions (25 Sep 2025; comply 1 Apr 2026): two distinct factors; card-not-present needs a dynamic one; issuers "may" offer choice; risk checks above the minimum allowed; SMS OTP not banned; non-compliant losses compensated in full [verified-primary] S61.
- Exempt (Annexure-1): small contactless, recurring after the first, some PPIs, NETC, offline small value, corporate-card travel, UPI Tap and Pay (2 Sep 2026). Cross-border CNP: by 1 Oct 2026 issuers validate non-recurring and risk-screen all [verified-primary] S61.
- Issuers, in-app/device approval: Axis in-app mobile OTP (TOTP) for net-banking login and optional transaction auth (7 Apr 2025) [verified-primary] S63; fingerprint/Face ID approval of card payments on Flipkart via PayU/Wibmo, Axis cards only (28 Apr 2026; OTP fallback unstated) [verified-primary] S64. IDFC FIRST: Visa Payment Passkey live (2 Jul 2026) [reported-secondary] S66. Razorpay + Mastercard passkey (31 Mar 2026), issuers unnamed [verified-primary] S65. HDFC, ICICI, SBI Card: no evidence found; HDFC pages show SMS OTP at checkout [verified-primary] S62.

## Gaps
NPCI P2M-collect circular; OC-220 text; BillDesk/PhonePe PG collect status; HDFC/ICICI/SBI Card in-app approval; NPCI maximum mandate tenure; NACH 2025-26 circular text; portal CAPTCHA; bare upi:// tappability in chat apps.

## Source key (date)
S1 7 Oct 2025 https://www.npci.org.in/uploads/UPI_OC_No_223_FY_2025_26_Enhancement_of_UPI_Autopay_88b38535cb.pdf
S2 undated https://razorpay.com/docs/payments/recurring-payments/autopay-interoperability
S3 31 Aug 2026 https://www.medianama.com/2026/08/223-npci-upi-autopay-interoperability/
S4 23 Feb 2026 https://www.angelone.in/news/market-updates/rbi-asks-npci-to-review-rise-in-upi-autopay-debit-complaints-report
S5 8 Oct 2025 https://www.npci.org.in/uploads/RBI_Governor_unveils_New_Generation_of_Digital_Payment_Initiatives_at_GFF_6494fabdfc.pdf
S6 23 Oct 2025 https://www.angelone.in/news/personal-finance/npci-launches-ai-powered-upi-help-to-assist-users-with-digital-payments
S7 10 Sep 2026 https://www.npci.org.in/uploads/NPCI_Press_Release_RBI_Governor_Unveils_New_UPI_Capabilities_at_GFF_2026_AI_Powered_Customer_Support_and_Seamless_Tap_and_Pay_Experience_bb763a7670.pdf
S8 fetched 14 Oct 2026 https://upihelp.npci.org.in
S9 25 Dec 2025 https://www.obnews.co/Index/newsDetail/id/13197206.html ; 2 Jan 2026 https://www.codeforbanks.com/banks/blog/all-about-npcis-newly-launched-portal-upihelp/
S10 15 Jan 2026 https://trackautopay.com/how-to-check-upi-autopay-mandates
S11 21 Apr 2026 https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13374
S12 16 Sep 2019 https://visa.co.in/about-visa/newsroom/press-releases/visa-to-partner-with-billdesk-for-rollout-of-si-hub-solution-for-recurring-payments.html
S13 undated https://razorpay.com/docs/announcements/rbi-card-mandate-guidelines/recurring-payments/mandatehq-faqs/
S14 2026 https://www.icici.bank.in/personal-banking/cards/merchant-standing-instructions
S15 2026 https://www.axis.bank.in/important-links/credit-card/recurring-payments
S16 14 Oct 2026 https://www.federal.bank.in/e-mandate-registration-for-cards
S17 undated https://www.hsbc.bank.in/help/faqs/e-mandates
S18 undated https://www.hdfc.bank.in/cards/services/emandate-for-debit-credit-cards
S19 8 Sep 2026 https://razorpay.com/blog/razorpay-expands-mandatehq-to-power-hdfc-bank-with-a-unified-card-mandate-experience/
S20 2 Mar 2024 https://sbi.bank.in/web/personal-banking/faq-on-e-mandate-on-debit-cards
S21 undated https://www.sbicard.com/en/personal/benefits/utility-bill-payments/recurring-e-mandates.page
S22 2021 https://www.idfcfirst.bank.in/content/dam/idfcfirstbank/FAQs/RecurringPayments.pdf
S23 Apr 2026 https://www.capital.bank.in/downloads/mandate_cancellation_user_guide.pdf
S24 undated https://punjabandsind.bank.in/content/nach-faq
S25 undated https://www.indusind.bank.in/content/enach/welcome.html
S26 8 Jan 2026 https://razorpay.com/blog/how-to-cancel-nach-mandate/
S27 25 May 2026 https://www.fidcindia.org.in/wp-content/uploads/2026/05/FIDC-RBI-CGM-DSSP_eNACH_25May2026.pdf
S28 28 Apr 2026 https://teamleaseregtech.com/updates/article/55357/npci-revision-of-nach-mandate-form/
S29 undated https://razorpay.com/docs/payments/payment-gateway/s2s-integration/recurring-payments/upi.md ; https://razorpay.com/docs/payments/recurring-payments/upi/faqs.md
S30 undated https://docs.juspay.io/mandates/docs/how-to-integrate/troubleshooting--faqs
S31 undated https://razorpay.com/docs/api/payments/tpap-pro/mandate-flow/pause-resume-mandate.md
S32 undated https://docs.juspay.io/lotuspay/web/nach-debit-flow/mandate-cancellation
S33 12 Aug 2025 https://www.teamleaseregtech.com/updates/article/45492/npci-issued-a-notification-regarding-the-discontinuation-of-the-servic/
S34 13 Aug 2025 https://www.businesstoday.in/amp/personal-finance/banking/story/npci-to-tighten-upi-rules-peer-to-peer-collect-feature-to-end-in-october-489244-2025-08-13
S35 undated https://razorpay.com/docs/announcements/upi-collect-migration/s2s-integration
S36 8 Jun 2026 https://docs.payu.in/docs/upi-collect-disablement-information
S37 undated https://www.cashfree.com/docs/payments/manage/payment-methods/upi-collect.md
S38 undated https://juspay.io/in/docs/ec-api/docs/base-integration/upi-collect-payment
S39 10 Nov 2025, 10 Mar 2026 https://docs.billdesk.io/reference/validatvpa.md ; https://docs.billdesk.io/docs/upi-intent-in-webview.md
S40 13 Oct 2026 https://www.npci.org.in/uploads/UPI_MDR_on_Select_Merchant_Transactions_Shall_be_Implemented_Post_Diwali_4c9cdd77d5.pdf
S41 13 Oct 2026 https://www.tribuneindia.com/news/digital-payments/npci-defers-mdr-on-select-upi-merchant-transactions-post-diwali
S42 Nov 2017 https://www.labnol.org/files/linking.pdf
S43 undated https://razorpay.com/docs/api/qr-codes/entity.md
S44 undated https://juspay.io/in/docs/upi-qr-code
S45 undated https://razorpay.com/docs/payments/payment-methods/upi/
S46 21 Nov 2025 https://support.setu.co/support/solutions/articles/81000419881-can-we-create-psp-specific-upi-deeplinks-
S47 undated https://juspay.io/mweb-intent/docs/upi-intent-on-mweb/troubleshooting--faqs
S48 2026 https://www.paytmpayments.com/docs/integration-steps-for-ios
S49 9 Oct 2025 https://razorpay.com/newsroom/razorpay-npci-and-openai-come-together-to-launch-agentic-payments-ushering-in-ai-driven-commerce-at-national-scale/
S50 9 Oct 2025 https://techcrunch.com/2025/10/09/india-pilots-ai-chatbot-led-e-commerce-with-chatgpt-gemini-claude-in-the-mix
S51 20 Feb 2026 https://razorpay.com/newsroom/razorpay-npci-launch-agentic-payments-on-claude-powering-zomato-swiggy-zepto-at-the-india-ai-impact-summit/
S52 12 Mar 2026 https://razorpay.com/blog/upi-reserve-pay/
S53 undated https://razorpay.com/agentic-payments/
S54 undated https://www.pinelabs.com/docs/online-payments/upi-reserve-pay/faqs
S55 28 Aug 2024 https://fintechchronicler.substack.com/p/npci-s-upi-circle-what-you-need-to
S56 29 Sep 2026 https://www.medianama.com/2026/09/223-npci-ai-agents-upi-payments/
S57 9 Jul 2026 https://visionias.in/current-affairs/upsc-daily-news-summary/article/2026-07-09/business-standard/economy/india-may-allow-agentic-ai-led-upi-transactions-under-new-npci-protocol
S58 1 Sep 2026 https://inc42.com/buzz/npci-to-launch-agentic-payments-on-upi-report/
S59 Feb 2026 https://www.mastercard.com/news/ap/en-in/newsroom/press-releases/en-in/2026/february/mastercard-demonstrates-readiness-for-a-new-era-of-ai-powered-payments-in-india-with-its-first-authenticated-agentic-commerce-transaction/ ; 23 Feb 2026 https://www.paymentsjournal.com/mastercard-advances-agentic-ai-commerce-in-india/
S60 10, 15 Sep 2026 https://indiantelevision.com/iworld/visa-demonstrates-agentic-commerce-payments-at-gff-2026/ ; https://retailintel.in/signal/visa-india-backs-trusted-ai-agents-tokenised-payments-for-ag-f0e1156b
S61 25 Sep 2025 (page amended Sep 2026) https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12898
S62 undated https://www.hdfc.bank.in/blogs/credit-cards/what-is-otp-in-credit-card-transaction ; https://www.hdfc.bank.in/credit-cards/services/enhanced-security-for-credit-cards-transactions
S63 7 Apr 2025 https://www.axis.bank.in/about-us/press-releases/axis-bank-introduces-industry-first-in-app-mobile-otp-to-enhance-digital-security
S64 28 Apr 2026 https://www.axis.bank.in/about-us/press-releases/flipkart-axis-bank-and-payu-introduce-biometric-authentication-for-card-payments
S65 31 Mar 2026 https://razorpay.com/newsroom/say-goodbye-to-otps-razorpay-along-with-mastercard-and-visa-launches-rbi-compliant-biometric-passkey/
S66 2 Jul 2026 https://www.businesstoday.in/amp/personal-finance/news/story/waiting-for-otp-to-make-a-card-payment-visa-will-now-allow-payment-authentication-via-passkey-540500-2026-07-02
S67 14 Oct 2026 https://www.cashfree.com/whatsapp-payment-links/
S68 undated https://www.infobip.com/docs/whatsapp/whatsapp-payments/india.md ; https://developers.facebook.com/documentation/business-messaging/whatsapp/payments/payments-in/enhanced-payment-links
