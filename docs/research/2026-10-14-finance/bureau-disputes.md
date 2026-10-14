# Credit-bureau (CIC) disputes in India: desk brief, 14 Oct 2026

Method: WebSearch/WebFetch only; static public pages, no flow executed, nothing logged into or submitted. Tags: [verified-primary] = read on the regulator's or bureau's own page/text; [reported-secondary] = lender/fintech/press page, reproduction or search-engine excerpt; [unconfirmed] = not found or inferred. "S#" = sources at the end, with dates.

## 0. Access caveats
- cibil.com, myscore.cibil.com, ssp.cibil.com returned HTTP 403 to my fetcher (14 Oct 2026); cause unconfirmed. All CIBIL facts below are [reported-secondary] (excerpts of cibil.com pages, S16; guides S17-S19).
- RBI's PDF host served an image-code bot challenge (not attempted); I read rbi.org.in HTML pages and reproductions (S5, S6).
- Experian, Equifax and CRIF consumer apps are JS single-page apps; a plain fetch returns only a title [verified-primary, own fetches].
- Published CAPTCHA, rate-limit or session-timeout rules for any CIC: none found [unconfirmed].

## 1. Dispute flow per CIC
**TransUnion CIBIL**
- Entry: myCIBIL login, Credit Reports, Dispute Centre, "Dispute an Item"; or public form cibil.com/consumer-dispute-resolution with mandatory "Control No." plus name, address, DOB, email, gender, one ID/phone [reported-secondary; S16, S17].
- Needs a fetched report: control number, 9 digits, top right, new per pull [reported-secondary; S18]; reports reportedly must be under 60 days old [reported-secondary; S16].
- Types (individual): personal, contact, employment, account, ownership, duplicate, enquiry. Not disputable: account numbers, member names, enquiry dates, control numbers [reported-secondary; S17].
- Upload mechanism unconfirmed; ID proof (PAN/DL/passport/voter ID) cited [reported-secondary; S17].
- Reference: Dispute ID emailed (CIBIL's example "P26052025199693") plus Service Request No. for escalation [reported-secondary; S16].
- Tracking: "Under Dispute" flag, email every 7 days, SMS on correction, My Disputes > View Status. Stated turnaround about 30 days, subject to lender [reported-secondary; S16, S17].

**Experian India**
- Entry: consumer.experian.in/ECSINDIA-DCE/. The form needs ERN (top right of report) plus UTI (in the "Experian Credit View" email), both under 30 days old, else fetch a fresh report; logging in (mobile + password, Dispute menu) leads to the same form. Phone, email, walk-in also work [verified-primary; S8, S9].
- Wait 30 days after payment/closure before disputing [verified-primary; S8].
- Types: Account Details, Personal Details, Credit Enquiry; edit values in-field; pop-up offers bank details for compensation; email acknowledgement [verified-primary; S8]. Upload: none shown [unconfirmed].
- Reference/ticket ID by email; "Track dispute" in dashboard; 30 calendar days; rejections go to the Internal Ombudsman [verified-primary; S8, S9]. Experian cannot change data without the lender's consent [verified-primary; S8].

**Equifax India**
- Entry: online form d2c.equifax.co.in/gcs/registration.jsp#/gcs/portalRouter/consumer-dispute (track: .../track-your-dispute), email ecissupport@equifax.com, post. Phone gives guidance only; no dispute accepted by call [verified-primary; S14].
- Needs Report Order Number, dispute type (ownership/balance/status/other), documents if any (e.g. bank NOC). Classes: False Positive, False Negative, Member-to-Report. 30 days (21 CI + 9 CIC) [verified-primary; S14].
- Identity checks, upload method, reference ID: not stated [unconfirmed].

**CRIF High Mark**
- Entry: Dispute Origination System via crifhighmark.com/raise-a-dispute, cir.crifhighmark.com; OTP identity check; My Report, Raise a Dispute, pick Report ID, account and reason, edit values, bank/UPI (or opt out), submit [verified-primary; S15 DOS blog].
- Types: Personal, Contact, KYC, Account (wrong or not-yours accounts, repayment history), unsolicited enquiries; policy adds duplicate, ownership [verified-primary; S15].
- Upload: DOS page silent; an older page says email documents to customerservice@crifhighmark.com [verified-primary; S15].
- Request number by email; "Dispute History" tracking (page flags the link as broken); phone staff can raise it for a caller; 30 days; rejections auto-escalate to the Internal Ombudsman within 20 days [verified-primary; S15 policy v1.15].

## 2. Getting the report
- RBI: one free full report incl. score per CIC per calendar year (Jan-Dec), electronic, after authentication, link on CIC homepage [verified-primary; S2 para 2.8; reproduced S5 para 21(7)]. Origin: circular of 1 Sep 2016 [verified-primary; S15 CRIF policy].
- CIBIL: free once a year (next free 1 Jan 2027 if taken in 2026) [reported-secondary; S16]; PDF via "Print Report", password a six-digit code SMSed to the registered mobile [reported-secondary; S19].
- Experian: experian.in "Free Credit Score", register with the mobile linked to credit, OTP, name/email/DOB/ID/address/PIN, "Download Report" [verified-primary; S11]. T&Cs say once a year, FAQ says unlimited (conflict) [verified-primary; S12, S10]. WhatsApp +91 99200 35444 (2022) gives score and a password-protected emailed report [verified-primary; S13]. No password rule found; a forgotten password goes via a query [verified-primary; S10].
- Equifax: d2c portal, account (email, mobile, password), OTP, review DOB/address/PAN/Aadhaar, "Run your Free Report"; offline form plus ID/address proof, 7 days [verified-primary; S14]. PDF password: a lender site says DOB [unconfirmed].
- CRIF: free-flow page: name, ID type/number, mobile, email, OTP (mobile and/or email), optional financial questionnaire; My Report, Latest Report, Download; password-protected, first 4 letters of name + last 4 digits of mobile [verified-primary; S15].

## 3. RBI rules in force (14 Oct 2026)
- Clock: 30 calendar days from filing with CI or CIC; CI 21 days to send corrected data, CIC the other 9 [verified-primary; S1].
- Compensation: Rs 100 per calendar day beyond 30, paid within 5 working days of resolution to the bank/UPI on the form; CI pays if it misses 21 days, CIC if the CI was on time, pro rata otherwise; effective six months after 26 Oct 2023 (CIBIL: 26 Apr 2024); excludes CICRA s.18 disputes, score-model disputes, matters before other fora [verified-primary; S1; date S16]. Rejection alone earns none [reported-secondary; S16].
- Alerts: CICs SMS/email on access by specified users (only if the enquiry shows in the report); CIs SMS/email on default/DPD reporting; effective about 26 Apr 2024 [verified-primary; S2]. Experian notices name the institution [verified-primary; S10].
- Data frequency: fortnightly (15th, month-end) from 1 Jan 2025 [verified-primary; S3]. From 1 Jul 2026: reference dates 9th, 16th, 23rd, month-end; full file by the 5th; incrementals within 4 days; RBI calls it "weekly incremental" [verified-primary; S4]. The 29 Sep 2025 draft (7/14/21/28, from 1 Apr) is stale [reported-secondary; S27].
- Lender vs bureau: complaint may go to either [verified-primary; S1]; the CI corrects at source and the CIC cannot change data first [reported-secondary; S5 para 25(2)].
- Escalation: RB-IOS 2026 (in force 1 Jul 2026, replaces 2021) covers CICs: complain to the CIC/CI first; no reply in 30 days or dissatisfied; file within 90 days; free; cms.rbi.org.in, crpc@rbi.org.in or post; helpline 14448 [verified-primary; S7]. CICRA s.18 remedies excluded [reported-secondary; S7].

## 4. Categories and evidence
- Categories: not-mine/ownership, duplicates, status (open/closed/settled), amount/DPD, personal/contact/KYC, unrecognised enquiry, missing account [verified-primary; S14, S15].
- Evidence cited: NOC/closure letter on lender letterhead (name, loan number, closure date) plus bank statement of final payment [reported-secondary; S21]; settlement letters, repayment receipts [reported-secondary; S22]. FIR/police complaint for fraud accounts: no bureau or RBI source found [unconfirmed].

## 5. Automation friction
- Experian needs a UTI from an email; ERN/UTI lapse at 30 days [verified-primary; S8].
- OTP at registration/identity check: Experian, Equifax, CRIF [verified-primary; S11, S14, S15]; CIBIL OTP, with user reports of missing OTPs and no OTP box [reported-secondary; S23].
- Knowledge questions: CRIF optional financial questionnaire [verified-primary; S15]; none found elsewhere [unconfirmed].
- Outputs: CIBIL and CRIF reports are PDFs behind passwords (section 2); Equifax format unspecified [unconfirmed].
- CRIF flags closed disputes re-raised via CMS/CPGRAMS on the same tradeline and field [verified-primary; S15].
- Experian terms: one person per form, no queries about others, rights non-transferable [verified-primary; S12]. No anti-bot clause found in Equifax/Experian terms [unconfirmed].

## 6. Third parties
- No RBI or bureau rule found expressly allowing or forbidding an app raising a bureau dispute for a consumer [unconfirmed].
- CICs may share a report with entities that are not specified users on the individual's written consent, the entity acting as "authorised representative": limited end use, 6-month retention, India-only storage, annual audit [reported-secondary; S5 para 24]. Live example: Experian-hosted consent making a finance company the consumer's authorised representative [verified-primary; S24].
- RB-IOS 2026 lets a representative appointed in writing (not an advocate) file [verified-primary; S7].
- CRIF accepts lender-raised disputes (consent letter needed when a lender acts for an entity) [verified-primary; S15]; Experian dropped lender-by-email disputes on 28 Mar 2025 [verified-primary; S9].
- Fintechs: OneScore "Report an error" hands off to CIBIL's Dispute Centre [reported-secondary; S25]. CreditMantri "Credit Improvement Services" helps raise disputes and agrees repayment plans with lenders; no price or authorisation form shown [verified-primary; S26]. Kenstone Credit runs disputes for Rs 9,999 (one bureau) or Rs 14,999 (four) plus 18% GST, "written consent" [verified-primary; S26]. No RBI/CIBIL advisory on credit-repair firms found [unconfirmed].

## Sources
S1 https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12554 [26 Oct 2023]
S2 https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12553 [26 Oct 2023]
S3 https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12718 [8 Aug 2024]
S4 https://www.crifhighmark.com/media/5913/reserve-bank-of-india-credit-information-companies-amendment-directions-2025.pdf [RBI text, 4 Dec 2025]; https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=61742 [4 Dec 2025]
S5 https://taxguru.in/rbi/rbi-credit-information-companies-directions-2025.html [RBI/DOR/2025-26/378, 28 Nov 2025]
S6 https://taxguru.in/rbi/master-direction-reserve-bank-india-credit-information-reporting-directions-2025.html [6 Jan 2025]
S7 https://www.rbi.org.in/commonperson/english/scripts/FAQs.aspx?Id=3407 [1 Jul 2026]; https://taxguru.in/rbi/rbi-issues-reserve-bank-integrated-ombudsman-scheme-2026.html [16 Jan 2026]
S8 https://experian.in/wp-content/uploads/sites/27/2024/10/Experian-India-How-to-raise-a-dispute-guide.pdf [file dated 21 Apr 2025]
S9 https://www.experian.in/wp-content/uploads/sites/27/2023/11/Consumer-Complaint-Redressal-Policy.pdf [v6, 30 Jun 2026]
S10 https://www.experian.in/consumer/faq/ [undated]; https://www.experian.in/wp-content/uploads/sites/39/2026/03/Frequently-Asked-Questions-V8.pdf [Mar 2026]
S11 https://experian.in/wp-content/uploads/sites/27/2024/10/Experian-Credit-Report-Download-Guide.pdf [22 Oct 2024]
S12 https://consumer.experian.in/ECV-OLN/Terms%20and%20Conditions%20-%20Online%20Experian%20CIR.pdf [undated]
S13 https://www.experianplc.com/newsroom/press-releases/2022/experian-becomes-first-credit-bureau-in-india-to-offer-free-credit-scores-on-whatsapp [9 Nov 2022]
S14 https://www.equifax.co.in/consumer/support/consumer-redressal [(c)2026]; https://assets.equifax.com/marketing/india/assets/customer_grievance_redressal_2025.pdf [Jun 2025]; https://assets.equifax.com/assets/india/free-Credit-Report_Web%20Site_24.02.22%20copy.pdf [24 Feb 2022]
S15 https://www.crifhighmark.com/blog/use-crif-dispute-origination-system-dos [(c)2025]; https://crifhighmark.com/news-events/news/check-credit-history [(c)2025]; https://www.crifhighmark.com/media/5915/consumer-grievance-redressal-policy.pdf [27 Nov 2025]; https://www.crif.in/media/1592/free-credit-report-policy-v11.pdf [5 Apr 2018]
S16 https://www.cibil.com/consumer-dispute-resolution ; https://www.cibil.com/framework-for-compensation ; https://www.cibil.com/freecibilscore (search excerpts, 14 Oct 2026; pages undated)
S17 https://www.bankbazaar.com/cibil/cibil-dispute.html [14 Oct 2026]; https://moneyview.in/cibil-score/how-to-raise-dispute-in-cibil [2026]
S18 https://moneyview.in/cibil-score/what-is-control-number-in-cibil [undated]
S19 https://moneyview.in/cibil-score/what-is-cibil-report-and-how-to-download-it [undated]
S21 https://www.bajajhousingfinance.in/resources/how-to-upload-noc-in-cibil [undated, search excerpt]
S22 https://www.adityabirlacapital.com/loans/blogs/personal-finance/all-you-need-to-know-about-cibil-dispute [7 Sep 2026]
S23 https://technofino.in/community/threads/not-receiving-otp-from-cibil.3879/latest [undated]
S24 https://consumer.experian.in/ecv-jet/Experian%20Terms%20and%20Condition.pdf [undated]
S25 https://m.thewire.in/article/ptiprnews/how-to-fix-errors-in-credit-reports-and-boost-scores-with-onescore [26 May 2025]
S26 https://www.creditmantri.com/credit-improvement-services/ [(c)2026]; https://kenstonecredit.com/personal-credit-repair [(c)2026]
S27 https://website.rbi.org.in/web/rbi/-/notifications/reserve-bank-of-india-credit-information-reporting-1st-amendment-directions-2025-draft [29 Sep 2025, search excerpt]
