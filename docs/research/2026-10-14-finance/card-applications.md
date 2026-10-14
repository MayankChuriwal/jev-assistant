# Credit-card applications in India via an assistant: research brief (14 Oct 2026)

Tags: [verified-primary] = read on the regulator/issuer page itself; [reported-secondary] = law-firm, press, affiliate, or search-tool extract of a page I could not open; [unconfirmed] = not found. [n] = source list; "undated" = no date on page (fetched 14 Oct 2026). Search quota ran out mid-research; applyonline.hdfc.bank.in, au.bank.in, paisabazaar.com (HTTP 403) and americanexpress.com (error) were unreadable, so those facts are search extracts. RBI PDFs are CAPTCHA-gated (not attempted); I used RBI HTML pages and a TaxGuru reproduction.

## 1. Issuer journeys
- Skeleton: mobile+OTP, PAN/DOB/income/employer/address/pincode, bureau consent, KYC (Aadhaar OTP e-KYC, CKYC, V-KYC or document pick-up), decision, reference no., status page. Aadhaar is optional in law (KYC para 23) [verified-primary][4].
- Axis (web, new-to-bank): step 1 = Aadhaar-linked mobile, PAN, pincode, net income, CAPTCHA, CKYC no. + OTP to CKYC-registered mobile; then card, pre-filled personal/employer/address data, OTPs for personal and office email. Consents: OTP terms; "consent to conduct credit bureau enquiry"; WhatsApp marketing; optional insurance [verified-primary][16]. V-KYC: Application ID (SMS)+OTP, Credit Card Dept official live, hard-copy PAN (OCR), face match, 8am-10pm, link valid 72h; tracker sparsh.axis.bank.in [verified-primary][17]. Confirmation gives reference ID and an "instant" card subject to policy [reported-secondary][16].
- HDFC: finder asks occupation + income [verified-primary][19]. Extracts: eligibility by mobile+DOB, Aadhaar OTP e-KYC, V-KYC link (SMS, 3 days, original PAN, in India), 45-day lapse, delivery 7-10 working days [reported-secondary][19,20]. Track: mobile + Application Reference/Form No. or DOB [verified-primary][18].
- SBI Card (NBFC): FAQ (c2026) does not claim an instant decision; approved applicants get a call within 2 working days and document pick-up; TAT up to 21 days; reapply after 3 months [verified-primary][21]. Track by application/reference no.; resume via name+mobile+OTP; approval SMS links an e-card [22]. Docs self-attested, 5 MB [23]. WhatsApp onboarding (Mar 2024), resume nudges (Oct 2025) [24]. V-KYC with Aadhaar XML, PAN OCR, geotag, e-sign: 2020 description [reported-secondary][25].
- ICICI: web = form, upload KYC+income docs, "Track Applications" [verified-primary][26]; pre-approved (existing customers, iMobile): pick offer, T&C, one OTP, "no additional documentation or credit checks" [verified-primary][27]. Card V-KYC since June 2020 [28].
- Kotak: card, name/phone/PAN/Aadhaar, V-KYC; approval up to 10 working days; track by application no. or SMS "CCAPP <13-digit no.>" to 5676788 [verified-primary][29,30].
- IDFC FIRST (extract): phone OTP, PAN+income, instant V-KYC (existing customers skip); tracker DOB+mobile or application no., then OTP [reported-secondary][31]. AU SFB (extract): AU 0101 app: mobile+PAN, Aadhaar verification, employer, address-proof upload, V-KYC [32]. Amex (extract, ~4 yrs old): form, OVD upload or Aadhaar-OTP V-KYC; success notice within 15 days, else up to 90 [33]. AU/Amex status pages [unconfirmed].
- App/chat-only: ICICI pre-approved, SBI WhatsApp [24], co-brand fintech cards (Jupiter, Rio; Kiwi virtual) [reported-secondary][36].
- Instant decision: ICICI pre-approved (T&C+OTP) [27]; Axis, IDFC claim it (extracts); SBI FAQ does not [21]; Kotak FAQ up to 10 working days [29]. In-person fallback: SBI document pick-up [21]; HDFC representative if Aadhaar OTP fails (extract) [19]. E-sign: only SBI's 2020 flow names it [25]; Axis, ICICI record acceptance by OTP [16,27].
- Soft vs hard: Axis wording is the only issuer bureau consent I could read; no Indian rule defining soft/hard found [unconfirmed].

## 2. Aggregators
- BankBazaar: details+mobile, OTP, matching cards, submit; also says apply on issuer site; "each application triggers a hard inquiry" [verified-primary][34].
- Paisabazaar (extracts): mobile+OTP, occupation, eligible cards, "Grab Now", PAN, parents' names; partner banks'/NBFCs' cards, issuer decision final; self-checks and pre-approved checks soft, applications hard [reported-secondary][35].
- Hand-off: no source states it; affiliate rules show completion in the issuer funnel (V-KYC, activation) [reported-secondary][37]. DSAs/DMAs only solicit/service; issuer decides and is liable for agents (banks paras 11(10), 41, 44) [verified-primary][2,3].
- Jan 2025: CICs SMS/email customers on lender pulls; non-lender entities may fetch a report as authorised representative on prior written verifiable consent, delete after 6 months [reported-secondary][15].

## 3. Rules in force
- Cards: the 2022 Master Direction was replaced 28 Nov 2025 by entity-wise Directions (banks /155 para 93; NBFCs /348 para 87; SFBs separate, unread). Only 2026 bank-card amendment listed: 27 Apr 2026, para 23(5), past-due after >3 days, from 1 Apr 2027 [verified-primary][1,3,5].
- Conduct (banks para 11 / NBFC 12) [verified-primary][3; banks 2]: explicit consent, written or digital mode with multifactor authentication (8); unsolicited cards banned, charges reversed + 2x penalty (4); one-page Key Fact Statement with application, written rejection reasons (1); MITC highlighted, English/Hindi/local language, sent with signed/digitally signed agreement (2); OTP consent to activate after 30 days inactive, else closure within 7 working days free (6); no CIC reporting before activation (7); issuer alone decides (10); calls 10:00-19:00 (9); no unilateral limit rises (banks para 34).
- KYC (banks, /169; 18 Sep 2026 amendment only redefines "certified copy") [verified-primary][4,6]: OTP e-KYC (para 25) needs specific consent, OTP to Aadhaar-registered mobile, borrowal = term loans up to Rs 60,000/yr, cards unmentioned; Aadhaar face authentication only by bank staff/BC; CKYC download needs explicit consent.
- V-CIP (paras 26-27) [verified-primary][4]: run only by trained bank officials; official captures the customer "present for identification"; prompting at customer end = reject; BC may assist only customer-side; live GPS, non-Indian IPs blocked, varied questions, dropped session = new session, PAN shown live, offline Aadhaar XML at most 3 working days old, concurrent audit. The customer must be live; nobody else may operate or coach.
- Aadhaar Act: prior consent (s.8); approved private entities may authenticate since Jan 2025; s.40 penalties [reported-secondary][14]. A user-side agent relaying an Aadhaar OTP: [unconfirmed].
- DPDP: Rules notified 14 Nov 2025 per PIB, 18-month phasing [verified-primary][7]; G.S.R. 846(E) is dated 13 Nov per law firms. Rule 4 consent managers and Act s.6(9): 13 Nov 2026; Rules 3, 5-16, 22-23 (notice, consent, safeguards, breach, rights): 13 May 2027; IT Act s.43A repealed at that tranche, so duties are not yet enforceable [reported-secondary][8,9,11]. An assistant company deciding why/how data is processed fits PIB's Data Fiduciary definition (my inference) [7]; whether it must register as Consent Manager [unconfirmed]. Once live: standalone notice, encryption/masking/logs, breach report to Board within 72h, 90 days for rights requests, penalties to Rs 250 cr [7,8,10]. MeitY's Jan 2026 plan to cut to 12 months is a proposal [12]; a Removal-of-Difficulties Order (5 Oct 2026) reportedly edits wording; one report cites 13 Mar 2027 for ss.9-10 [unconfirmed][13].
- Digital Lending Directions (8 May 2025) cover digital loans/LSPs; card coverage [unconfirmed].

## 4. Friction for automation
- CAPTCHA: Axis form has one before step 1 [16]; others unstated.
- OTPs: Axis up to 4 (CKYC mobile, 2 emails, V-KYC) [16,17]; HDFC Aadhaar OTP + V-KYC; ICICI pre-approved 1 [27]; activation OTP later. OTP e-KYC uses the Aadhaar-registered mobile only [4]; HDFC affiliate terms require an Aadhaar-linked number [37].
- V-KYC: live, in India, camera/mic/location; Axis 8am-10pm [17]; HDFC (deposit-KYC page) 10am-11:59pm, no VPN/proxy, colour PAN copy rejected [verified-primary][20]; V-KYC >50 km from Aadhaar address rejected in HDFC affiliate terms [reported-secondary][37].
- Timeouts: V-KYC links 72h (Axis), 3 days (HDFC); SBI resume by OTP; HDFC 45-day lapse (extract); form idle timeouts [unconfirmed].
- Bot detection: my plain fetches got 403 at HDFC apply, AU, Paisabazaar; image CAPTCHA at RBI PDFs; cause [unconfirmed].
- Domains: banks had to move to bank.in by 31 Oct 2025 [reported-secondary][40]; my fetches were redirected.

## 5. Business
- Affiliate payout per card (Mar 2026): SBI Rs 1,890 (dispatch); AU 1,312-1,500 (dispatch); Axis 540-1,890 (activation); HDFC 1,050-2,700 (first txn); ICICI 375 (approval); IDFC 1,012 (delivery+fee); HSBC 3,037. Validation: AU 4.6%, Axis 38%, HDFC 63%, IDFC 64%, ICICI 71%; paid 60-90 days after 15-45-day validation [reported-secondary][36]. HDFC listing (Oct 2026): up to Rs 1,260 (Swiggy 2,195) on activation+first txn; bureau 780+; new-to-credit barred; Rs 320 cut for physical KYC [37]. EarnKaro IDFC Rs 350-1,225 [38]. Issuer DSA rate cards [unconfirmed].
- Players: Paisabazaar (PB Fintech: 3.5 lakh cards FY26, one source; credit revenue Rs 123 cr Q4 FY26) [39], BankBazaar, Cuelinks, EarnKaro, app-led co-brand issuers.

## Sources
[1] https://rbi.org.in/ScriptS/BS_ViewMasDirections.aspx?id=13155 (28 Nov 2025; upd. 1 Oct 2026)
[2] https://taxguru.in/?p=1005490 (text of [1], 28 Nov 2025)
[3] https://rbi.org.in/ScriptS/BS_ViewMasDirections.aspx?id=12956 (NBFC, 28 Nov 2025)
[4] https://rbi.org.in/ScriptS/BS_ViewMasDirections.aspx?id=13141 (KYC, 28 Nov 2025; upd. 1 Oct 2026)
[5] https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13388 (27 Apr 2026); index https://rbi.org.in/scripts/Fs_AmendmentDirections.aspx (14 Oct 2026)
[6] https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13699 (18 Sep 2026)
[7] https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf (17 Nov 2025)
[8] https://ssrana.in/articles/meity-notifies-final-digital-personal-data-protection-rules-2025/ (14 Nov 2025)
[9] https://www.amsshardul.com/insight/enforcement-of-the-dpdp-act-and-notification-of-the-dpdp-rules/ (21 Nov 2025)
[10] https://www.scconline.com/blog/post/2025/11/14/meity-notified-digital-personal-data-protection-rules-2025/ (14 Nov 2025)
[11] https://consentos.in/learn/dpdp-compliance-timeline/ (9 Oct 2026); https://www.networkintelligence.ai/blogs/dpdp-act-compliance-timeline-2026-2027/ (8 Oct 2026)
[12] https://www.storyboard18.com/amp/digital/meity-seeks-industry-views-on-fast-tracking-dpdp-act-rollout-proposes-12-month-compliance-timeline-88332.htm (28 Jan 2026)
[13] https://www.india-briefing.com/news/dpdp-removal-of-difficulties-order-2026-consent-data-audit-47274.html/ (7 Oct 2026)
[14] https://ssrana.in/articles/aadhaar-authentication-by-private-entities-from-data-privacy-perspective/ (31 Jul 2025)
[15] https://www.medianama.com/2025/01/223-rbi-mandates-stricter-norms-credit-information-sharing/ (7 Jan 2025)
[16] https://web.axis.bank.in/DigitalChannel/WebForm/NTBCustomer.html (undated)
[17] https://www.axis.bank.in/cards/credit-card-vkyc (undated)
[18] https://www.hdfc.bank.in/credit-cards/services/track-credit-card-status (undated)
[19] https://www.hdfc.bank.in/credit-cards (undated)
[20] https://online.hdfc.bank.in/onlinekyc/ (undated); https://v.hdfc.bank.in/htdocs/amp/learning-center/save/how-video-kyc-works.html (c2023)
[21] https://www.sbicard.com/en/faq/online-application.page (c2026)
[22] https://www.sbicard.com/en/eapply/track-credit-card-application.page (c2026)
[23] https://www.sbicard.com/en/eapply/docupload.page (c2026)
[24] https://www.sbicard.com/en/personal/digital-platform-updates.page (entries to May 2026)
[25] https://www.sbicard.com/sbi-card-en/assets/docs/pdf/who-we-are/news/2020/how-to-use-sbi-card-video-based-kyc-feature.pdf (16 Jun 2020)
[26] https://www.icici.bank.in/personal-banking/cards/credit-card/credit-card-eligibility-and-documents (undated)
[27] https://www.icici.bank.in/personal-banking/cards/consumer-cards1/credit-card/inaflash/index (undated)
[28] https://www.icici.bank.in/about-us/news-room/2020/news-icici-bank-launches-video-kyc-for-savings-account-personal-loan-and-credit-card (25 Jun 2020)
[29] https://www.kotak.bank.in/en/personal-banking/cards/credit-cards.html (notice dated Jul 2026)
[30] https://www.kotak.bank.in/en/personal-banking/cards/credit-cards/credit-card-services/sms-alerts.html (undated)
[31] https://idfcfirstbank.com/credit-card/track-my-application.html (undated, extract)
[32] https://www.au.bank.in/support/contact-us/retail/credit-card/apply-or-activate-credit-card/how-do-i-apply-for-credit-card-on-au-0101-app (undated, extract)
[33] https://www.americanexpress.com/in/content/credit-know-how/credit-card-application/ (undated, extract)
[34] https://www.bankbazaar.com/credit-card.html (undated)
[35] https://www.paisabazaar.com/pb-credit-card-exclusive-offers-terms-of-use ; https://www.paisabazaar.com/credit-score/soft-inquiry-vs-hard-inquiry-aio/ (undated, extracts)
[36] https://www.cuelinks.com/blog/best-credit-card-affiliate-programs/ (18 Mar 2026)
[37] https://www.cuelinks.com/campaigns/hdfc-bank-credit-card-affiliate-program (Oct 2026)
[38] https://earnkaro.com/blog/idfc-bank-affiliate-program/ (upd. Oct 2026)
[39] https://quartr.com/events/pb-fintech-policybzr-q4-25-26_FeB3abM1 (30 Jun 2026); https://inc42.com/buzz/pb-fintech-q4-profit-jumps-54-to-%E2%82%B9261-cr/ (6 May 2026)
[40] https://www.businesstoday.in/amp/personal-finance/banking/story/rbi-mandates-banks-to-adopt-bankin-domains-for-safer-digital-access-check-faqs-473150-2025-04-22 (22 Apr 2025)
