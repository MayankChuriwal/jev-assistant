# Voucher demo: implementation tasks

Spec: `docs/superpowers/specs/2026-10-13-voucher-demo-design.md`. Executed inline, test-first, one commit per task; the
unit and browser suites pass before each commit.

- [ ] 1. `final:` site-note flag: a control whose label starts with one of its comma-separated words is a commit
      outright (`gate.commit_label`). Tests in `tests/test_gate.py`.
- [ ] 2. A plain No to a purchase approval ends the task: outcome `not_approved`, result "You chose not to pay. Nothing
      was charged."; any other answer keeps today's Retry. Tests in `tests/test_gate.py` / `tests/test_worker.py`.
- [ ] 3. Brain: `PORTALS`, `voucher_options`, `voucher_checkout` (portal enum, named brand, read amounts in code, no
      re-read, `not_approved` mapped), playbook `skills/playbooks/vouchers.md`. Tests in `tests/test_brain.py`.
- [ ] 4. Site notes `skills/sites/www.gyftr.com.md`, `skills/sites/www.ishoprewards.com.md`,
      `skills/sites/payments.icici.bank.in.md`; worker `PROFILE_ASKS` and memory `LABELS` gain `icici_user_id`.
      Tests in `tests/test_worker.py` / `tests/test_skills.py`.
- [ ] 5. Starter card "Buy a voucher". Test in `tests/test_chat_page.py`.
- [ ] 6. Brain evals: three voucher cases in `assistant/brain_eval.py`; run them with the real model.
- [ ] 7. Dry run on the live sites, stopping before anything sends the user an OTP; fix what it shows.
