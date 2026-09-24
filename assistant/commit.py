"""Exact review, process-local approval, and durable single-dispatch primitives.

The coordinator proposal shape is unchanged. validate_review additionally needs a
separate extraction record: exponent, total_text, total_quote, amounts (line ID ->
original amount text), and line_quotes (line ID -> observed contiguous row).
Task 11/12 must obtain this record after Jev inspects all review/charge sections;
then re-extract and validate after approval. A coordinator proposal alone is not
an approval-ready review. Unknown fees and unresolved material choices block it.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import threading
import time
import unicodedata
import uuid
import weakref
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path

SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
# Review conversion uses code-owned exponents; unsupported currencies stay unresolved.
CURRENCY_EXPONENTS = dict.fromkeys("INR USD EUR GBP AUD CAD CHF CNY HKD SGD NZD AED SAR ZAR BRL MXN".split(), 2)
CURRENCY_EXPONENTS.update(JPY=0, KRW=0, KWD=3, BHD=3, OMR=3)
ROLES = {"item", "subtotal", "discount", "fee", "tax", "tip", "rounding", "later_estimate"}
LINE_KEYS = {"id", "label", "amount_minor", "currency", "role", "included_in", "evidence_ids"}


def money_minor(text: str, *, currency: str, exponent: int) -> int:
    """Accept dot decimals and unambiguous Western/Indian comma grouping only."""
    if (not isinstance(text, str) or len(text) > 128 or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency) or type(exponent) is not int
            or exponent not in {0, 1, 2, 3}):
        raise ValueError("money: unsupported currency, exponent or representation")
    value = text.strip()
    sign = ""
    if value.startswith(("-", "+")):
        sign, value = value[0], value[1:].strip()
    tokens = [currency] + ([SYMBOLS[currency]] if currency in SYMBOLS else [])
    for token in tokens:
        if value.startswith(token):
            value = value[len(token):].strip()
            break
        if value.endswith(token):
            value = value[:-len(token)].strip()
            break
    if not sign and value.startswith(("-", "+")):
        sign, value = value[0], value[1:]
    integer = r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]?(?:,[0-9]{2})+,[0-9]{3})"
    if not re.fullmatch(integer + r"(?:\.[0-9]+)?", value):
        raise ValueError("money: ambiguous separators or unsupported representation")
    with localcontext() as context:
        context.prec = 140
        minor = Decimal(sign + value.replace(",", "")) * (10 ** exponent)
        if minor != minor.to_integral_value():
            raise ValueError("money: fractional minor units")
        return int(minor)


def review_total(lines: list[dict], total_minor: int, *, currency: str) -> dict:
    """Validate the inclusion graph and coverage before summing payable roots."""
    reasons = []
    if (type(total_minor) is not int or total_minor < 0 or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency)):
        reasons.append("invalid total or currency")
    if not isinstance(lines, list) or not lines:
        return dict(valid=False, computed_minor=None, reasons=[*reasons, "missing breakdown"])
    by_id = {}
    for line in lines:
        if (not isinstance(line, dict) or set(line) != LINE_KEYS
                or any(not isinstance(line[k], str) or not line[k].strip() for k in ("id", "label"))
                or type(line["amount_minor"]) is not int or line["currency"] != currency
                or not isinstance(line["role"], str) or line["role"] not in ROLES
                or not isinstance(line["evidence_ids"], list) or not line["evidence_ids"]
                or not all(isinstance(e, str) and e for e in line["evidence_ids"])
                or (line["included_in"] is not None and not isinstance(line["included_in"], str))):
            reasons.append("invalid monetary line")
            continue
        if line["id"] in by_id:
            reasons.append("duplicate line ID")
        by_id[line["id"]] = line
        if ((line["role"] == "discount" and line["amount_minor"] > 0)
                or (line["role"] not in {"discount", "rounding"} and line["amount_minor"] < 0)):
            reasons.append("invalid signed amount")
    if reasons:
        return dict(valid=False, computed_minor=None, reasons=reasons)
    for line in lines:
        seen = {line["id"]}
        parent = line["included_in"]
        while parent is not None:
            if parent not in by_id or parent in seen:
                reasons.append("unknown inclusion reference or cycle")
                break
            seen.add(parent)
            if by_id[parent]["role"] == "later_estimate" and line["role"] != "later_estimate":
                reasons.append("payable line hidden in later estimate")
            parent = by_id[parent]["included_in"]
    roots = [line for line in lines if line["included_in"] is None and line["role"] != "later_estimate"]
    if not roots:
        reasons.append("missing payable coverage")
    if any(row["role"] == "subtotal" for row in roots) and any(row["role"] == "item" for row in roots):
        reasons.append("item/subtotal coverage is ambiguous")
    for subtotal in (row for row in lines if row["role"] == "subtotal"):
        children = [row for row in lines if row["included_in"] == subtotal["id"]
                    and row["role"] in {"item", "subtotal"}]
        adjustments = [row for row in lines if row["included_in"] == subtotal["id"]
                       and row["role"] in {"discount", "fee", "tip", "rounding"}]
        if children and sum(row["amount_minor"] for row in children + adjustments) != subtotal["amount_minor"]:
            reasons.append("item amounts do not cover subtotal")
    computed = sum(line["amount_minor"] for line in roots)
    if computed != total_minor:
        reasons.append("unexplained total mismatch")
    return dict(valid=not reasons, computed_minor=computed, reasons=reasons)


def _evidence_ids(value):
    return isinstance(value, list) and bool(value) and all(isinstance(e, str) and e.strip() for e in value)


def promotion_advice(base: dict, alternative: dict, terms: dict) -> dict:
    """Compare verified baskets; ineligibility and unknown conditions are distinct."""
    unknown = dict(eligible=False, extra_spend_minor=None, delivery_saving_minor=None, verified=False)
    for basket in (base, alternative):
        if (not isinstance(basket, dict) or not _evidence_ids(basket.get("evidence_ids"))
                or not isinstance(basket.get("currency"), str)
                or not re.fullmatch(r"[A-Z]{3}", basket["currency"])
                or any(type(basket.get(k)) is not int or basket[k] < 0
                       for k in ("total_minor", "delivery_minor", "qualifying_subtotal_minor"))
                or basket["delivery_minor"] > basket["total_minor"]):
            return unknown
    if (not isinstance(terms, dict) or base["currency"] != alternative["currency"]
            or not isinstance(terms.get("operator"), str) or terms["operator"] not in {"gt", "gte"}
            or not _evidence_ids(terms.get("evidence_ids"))
            or terms.get("conditions_verified") is not True or type(terms.get("eligible")) is not bool
            or type(terms.get("threshold_minor")) is not int or terms["threshold_minor"] < 0):
        return unknown
    subtotal, threshold = alternative["qualifying_subtotal_minor"], terms["threshold_minor"]
    threshold_met = subtotal > threshold if terms["operator"] == "gt" else subtotal >= threshold
    return dict(eligible=terms["eligible"] and threshold_met,
                extra_spend_minor=alternative["total_minor"] - base["total_minor"],
                delivery_saving_minor=base["delivery_minor"] - alternative["delivery_minor"], verified=True)


def _amount_in_quote(text, quote):
    # Substrings of larger numeric tokens are not observed amounts.
    return bool(re.search(r"(?<![0-9.,+\-])" + re.escape(text) + r"(?![0-9.,])", quote))


def _money_in_source(text, quote, source):
    """Keep adjacent currency/sign context at the exact row occurrence in the page.

    Checking only the model's row quote would allow it to trim a suffix currency
    from that quote as well as from the amount. Unsupported markers fail closed.
    """
    for row in re.finditer(re.escape(quote), source):
        for amount in re.finditer(r"(?<![0-9.,+\-])" + re.escape(text) + r"(?![0-9.,])", quote):
            start, end = row.start() + amount.start(), row.start() + amount.end()
            left, right = source[:start].rstrip(), source[end:].lstrip()
            if (left and (unicodedata.category(left[-1]) == "Sc" or left[-1] in "+-")):
                continue
            if right and unicodedata.category(right[0]) == "Sc":
                continue
            if re.search(r"(?<![A-Za-z])[A-Z]{3}$", left) or re.match(r"[A-Z]{3}(?![A-Za-z])", right):
                continue
            return True
    return False


def validate_review(state, proposal: dict, extraction: dict) -> dict:
    """Return a copied proposal only when current page evidence supports its review.

    This does not assert cart inspection is complete or authorize a commitment.
    Integration must resolve unread panels and material choices before calling it.
    """
    from .coordinator import _validate_proposal

    _validate_proposal(state, proposal)
    latest = next(reversed(state.observations), None)
    observation = state.observations.get(latest, {})
    completeness = observation.get("completeness", {})
    if any(v for k, v in completeness.items()
           if "truncat" in k or "missing" in k or "incomplete" in k or k == "omitted_actions"):
        raise ValueError("review: incomplete observation")

    def quotes(ids):
        if not isinstance(ids, list) or not ids:
            raise ValueError("review: missing evidence")
        result = []
        for eid in ids:
            e = state.evidence.get(eid, {})
            if (e.get("observation_id") != latest or e.get("revision") != state.revision
                    or e.get("kind") != "page"
                    or not _amount_in_quote(e.get("quote", ""), observation.get("text", ""))):
                raise ValueError("review: stale or non-page evidence")
            result.append(e["quote"])
        return result

    if re.search(r"(?<![0-9])(?:[0-9][ -]?){12,19}(?![0-9])", proposal["payment"]):
        raise ValueError("review: payment label must be masked")
    material = quotes(proposal["evidence_ids"])
    for value in [proposal[k] for k in ("service", "account", "destination", "option", "timing", "payment")] + (
            proposal["items"] + proposal["later_terms"]):
        if value and not any(_amount_in_quote(value, quote) for quote in material):
            raise ValueError("review: material detail absent from evidence")
    if proposal["total_kind"] == "not_applicable":
        return _reviewed(state, proposal, extraction)
    if not proposal["items"] or not proposal["destination"] or not proposal["timing"] or not proposal["payment"]:
        raise ValueError("review: missing material purchase details")
    if proposal["total_kind"] == "estimate" and not proposal["later_terms"]:
        raise ValueError("review: estimate needs observed variable-charge terms")
    if not isinstance(extraction, dict) or set(extraction) != {
            "exponent", "total_text", "total_quote", "amounts", "line_quotes"}:
        raise ValueError("review: missing original amount extraction")
    total_text, total_quote = extraction["total_text"], extraction["total_quote"]
    if (not isinstance(total_quote, str) or not isinstance(total_text, str) or not total_text
            or not _amount_in_quote(total_text, total_quote)
            or not _money_in_source(total_text, total_quote, observation["text"])
            or not any(_amount_in_quote(total_quote, q) for q in material)):
        raise ValueError("review: total not observed")
    currency, exponent = proposal["currency"], extraction["exponent"]
    if type(exponent) is not int or currency not in CURRENCY_EXPONENTS or exponent != CURRENCY_EXPONENTS[currency]:
        raise ValueError("review: unsupported currency exponent")
    if money_minor(total_text, currency=currency, exponent=exponent) != proposal["total_minor"]:
        raise ValueError("review: total extraction mismatch")
    ids = {line["id"] for line in proposal["lines"]}
    if (not isinstance(extraction["amounts"], dict) or not isinstance(extraction["line_quotes"], dict)
            or set(extraction["amounts"]) != ids or set(extraction["line_quotes"]) != ids):
        raise ValueError("review: missing line extraction")
    for line in proposal["lines"]:
        text, quote = extraction["amounts"][line["id"]], extraction["line_quotes"][line["id"]]
        if (not isinstance(text, str) or not text or not isinstance(quote, str)
                or not _amount_in_quote(text, quote) or line["label"] not in quote
                or not _money_in_source(text, quote, observation["text"])
                or not any(_amount_in_quote(quote, q) for q in quotes(line["evidence_ids"]))
                or money_minor(text, currency=currency, exponent=exponent) != line["amount_minor"]):
            raise ValueError("review: line extraction mismatch")
        if line["role"] == "later_estimate" and not proposal["later_terms"]:
            raise ValueError("review: later charges need disclosed terms")
    result = review_total(proposal["lines"], proposal["total_minor"], currency=currency)
    if not result["valid"]:
        raise ValueError("review: " + "; ".join(result["reasons"]))
    return _reviewed(state, proposal, extraction)


# Process-only authority is deliberately absent from TaskState/public_context and journals.
# Weak ownership also rejects copied/deserialized state carrying an old approval dict.
_RUNTIME = {}
_RUNTIME_LOCK = threading.RLock()


def _runtime(state):
    with _RUNTIME_LOCK:
        key = id(state)
        entry = _RUNTIME.get(key)
        if entry is None or entry['owner']() is not state:
            entry = {'owner': weakref.ref(state, lambda _: _RUNTIME.pop(key, None)),
                     'reviews': {}, 'decisions': {}, 'effects': {}, 'approval': None, 'lock': threading.RLock()}
            _RUNTIME[key] = entry
        return entry


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def proposal_key(proposal: dict) -> str:
    """Hash material details, preserving charge labels and inclusion relationships.

    Line IDs are extraction references, not material terms. Normalize them by their
    semantic contents so an equivalent fresh extraction can use new IDs.
    """
    ephemeral = {'evidence_ids', 'observation_id', 'display_text', 'action_label'}
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in ephemeral}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    material = clean(proposal)
    lines = material.get('lines', [])
    by_id = {row['id']: row for row in lines}
    def semantic(row, seen=()):
        if row['id'] in seen:
            raise ValueError('cyclic charge inclusion')
        result = {k: v for k, v in row.items() if k not in {'id', 'included_in'}}
        parent = row.get('included_in')
        result['included_in'] = semantic(by_id[parent], (*seen, row['id'])) if parent else None
        return result
    material['lines'] = sorted((semantic(row) for row in lines), key=_digest)
    return _digest(material)


def _reviewed(state, proposal, extraction):
    _runtime(state)['reviews'][_digest(proposal)] = dict(
        revision=state.revision, observation_id=next(reversed(state.observations), None),
        extraction=deepcopy(extraction))
    return deepcopy(proposal)


def _current_review(state, proposal):
    if not isinstance(proposal, dict):
        return False
    review = _runtime(state)['reviews'].get(_digest(proposal))
    if (not review or proposal.get('task_id') != state.task_id or proposal.get('revision') != state.revision
            or review['revision'] != state.revision
            or review['observation_id'] != next(reversed(state.observations), None)):
        return False
    try:
        validate_review(state, proposal, review['extraction'])
    except (ValueError, TypeError, KeyError):
        return False
    return True


def bind_proposal_effect(state, proposal: dict, *, operation: str, choice: str, key: str | None = None) -> str:
    """Bind a reviewed proposal to the exact observed effect presented for approval.

    The confirmation UI must show this control with the proposal, using the returned
    ID as its effect_id. This is code-owned linkage, not an executable model target.
    Re-extract it after observation; only equivalent unique control semantics retain
    authority across a cosmetic rerender. Ambiguous controls require more context.
    """
    if not _current_review(state, proposal):
        raise ValueError('effect requires a current validated proposal')
    oid = next(reversed(state.observations))
    observation = state.observations[oid]
    action = next((a for a in observation['actions'] if a['id'] == choice), None)
    if (action is None or operation not in {'CLICK', 'DOUBLE_CLICK', 'TYPE_TEXT', 'PRESS_KEY',
                                          'ACCEPT_DIALOG', 'LOOK'}
            or operation == 'PRESS_KEY' and key not in {'Enter', 'Space'}):
        raise ValueError('effect requires an observed submission control and operation')
    context = action.get('context')
    if (not isinstance(context, dict)
            or not any(isinstance(context.get(k), str) and context[k].strip()
                       for k in ('form', 'section', 'dialog'))):
        raise ValueError('effect requires observed form, section or dialog context')
    control = {k: v for k, v in action.items() if k != 'id'}
    if sum({k: v for k, v in a.items() if k != 'id'} == control for a in observation['actions']) != 1:
        raise ValueError('effect control is ambiguous; observe distinguishing context')
    material = dict(proposal_key=proposal_key(proposal), control=control, operation=operation, key=key,
                    origin=observation['origin'], url=observation['url'])
    effect_id = uuid.uuid4().hex
    _runtime(state)['effects'][effect_id] = dict(**deepcopy(material), effect_key=_digest(material),
        observation_id=oid, revision=state.revision, choice=choice)
    return effect_id


def _current_effect(state, effect_id):
    effect = _runtime(state)['effects'].get(effect_id)
    if (not effect or effect['revision'] != state.revision
            or effect['observation_id'] != next(reversed(state.observations), None)):
        raise ValueError('missing or stale observed proposal effect')
    return effect


def approve(state, proposal: dict, *, question_id: str, response: str) -> dict:
    """Only a displayed current confirmation can issue process-local authority."""
    runtime = _runtime(state)
    with runtime['lock']:
        q = state.questions.get(question_id, {})
        effect = _current_effect(state, q.get('effect_id'))
        current = [q for q in state.questions.values() if q.get('displayed_at') is not None
                   and q.get('answered_at') is None and not q.get('closed') and q.get('revision') == state.revision]
        if (state.status not in {'running', 'waiting_user'} or not _current_review(state, proposal)
                or len(current) != 1 or current[0] is not q or q.get('kind') != 'confirm'
                or q.get('proposal_key') != proposal_key(proposal)
                or effect['proposal_key'] != proposal_key(proposal)
                or not isinstance(response, str) or response.strip().casefold() not in {'yes', 'approve', 'confirm'}):
            raise ValueError('approval requires the current affirmative confirmation and validated review')
        approval = dict(id=uuid.uuid4().hex, proposal_key=proposal_key(proposal),
                        revision=state.revision, question_id=question_id, consumed=False,
                        effect_key=effect['effect_key'])
        state.approval = approval
        runtime['approval'] = {'object': approval, 'record': deepcopy(approval),
                               'observation_id': next(reversed(state.observations), None)}
        q['answered_at'] = time.time()
        return deepcopy(approval)


def can_commit(state, current: dict) -> bool:
    approval = state.approval
    return bool(approval and _runtime(state)['approval']
                and next(reversed(state.observations), None) != _runtime(state)['approval']['observation_id']
                and approval is _runtime(state)['approval']['object']
                and approval == _runtime(state)['approval']['record'] and not approval['consumed']
                and state.status in {'running', 'waiting_user'} and approval['revision'] == state.revision
                and _current_review(state, current) and approval['proposal_key'] == proposal_key(current))


def consume_approval(state, current: dict) -> str:
    with _runtime(state)['lock']:
        if not can_commit(state, current):
            raise ValueError('missing, stale or consumed approval')
        state.approval['consumed'] = True
        return state.approval['id']


class Journal:
    """Private, redacted append-only records. Pass only a code-owned path.

    create() allocates an opaque filename. An incomplete tail is readable but
    prevents further append; recovery must preserve the original journal.
    """
    def __init__(self, path: Path, redact):
        self.path, self.redact = Path(path).absolute(), redact
        self.lock = threading.RLock()

    @classmethod
    def create(cls, directory: Path, redact):
        return cls(Path(directory) / (uuid.uuid4().hex + '.jsonl'), redact)

    @contextmanager
    def _file(self):
        # Walk with directory descriptors: no parent component may redirect writes.
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        fd = None
        try:
            for component in self.path.parent.parts[1:]:
                if component in {'.', '..'}:
                    raise ValueError('invalid journal directory')
                try:
                    os.mkdir(component, 0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    # Persist each ancestry entry, including one left by a prior
                    # failed provisioning attempt, before descending into it.
                    os.fsync(directory)
                except BaseException:
                    os.close(child)
                    raise
                os.close(directory)
                directory = child
            os.fchmod(directory, 0o700)
            fd = os.open(self.path.name, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise ValueError('journal must be an owned regular file with one link')
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield fd
            os.fsync(directory)
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)

    @staticmethod
    def _read(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b''.join(chunks)

    def append(self, record: dict) -> None:
        data = (json.dumps(self.redact(deepcopy(record)), sort_keys=True, allow_nan=False) + '\n').encode()
        with self.lock, self._file() as fd:
            existing = self._read(fd)
            if existing and not existing.endswith(b'\n'):
                raise ValueError('journal has an incomplete tail; mutations must stop')
            # One append, never retry a short/failed persistence operation.
            if os.write(fd, data) != len(data):
                raise OSError('incomplete journal write')
            os.fsync(fd)

    def records(self) -> list[dict]:
        with self.lock, self._file() as fd:
            data = self._read(fd)
        lines = data.split(b'\n')[:-1]  # ignore only an incomplete final line
        result = [json.loads(line) for line in lines]
        if not all(isinstance(row, dict) for row in result):
            raise ValueError('invalid journal record')
        return result

    def interrupted(self) -> list[dict]:
        executions = {}
        for row in self.records():
            eid = row.get('execution_id')
            if eid:
                executions[eid] = {**executions.get(eid, {}), **row}
        return [dict(row, status='unknown') for row in executions.values()
                if row.get('phase') in {'dispatching', 'unknown'}
                or row.get('commitment') and row.get('phase') != 'not_dispatched'
                and not (row.get('phase') == 'outcome' and row.get('outcome') in {'success', 'failed'}
                         and row.get('evidence_ids'))]


def bind_decision(state, decision: dict, page: dict, *, assessment: str | None = None,
                  effect_id: str | None = None) -> dict:
    """Code-owned wrapper applied to a Jev decision at its observation/revision.

    Call before model work can be invalidated; do not rebind a stale model result.
    assessment must come from the validated coordinator/action-form assessment.
    """
    from .rules import commitment_kind
    from .state import _url_parts
    oid = next(reversed(state.observations), None)
    observation = state.observations.get(oid, {})
    action = next((a for a in page.get('actions', []) if a['id'] == decision.get('choice')), None)
    if (not isinstance(decision.get('id'), str) or not decision['id'] or action is None
            or observation.get('revision') != state.revision or observation.get('text') != page.get('text')
            or observation.get('url') != _url_parts(page.get('url'))[0]
            or not any(all(action.get(k) == v for k, v in observed.items())
                       for observed in observation.get('actions', []))):
        raise ValueError('decision has no current observed owner/target')
    bound = {**deepcopy(decision), 'task_id': state.task_id, 'revision': state.revision,
             'observation_id': oid}
    runtime = _runtime(state)
    effect = _current_effect(state, effect_id) if effect_id is not None else None
    if effect and (effect['choice'] != decision['choice'] or effect['operation'] != decision['operation']
                   or effect['key'] != decision.get('key')):
        raise ValueError('decision differs from its observed proposal effect')
    if decision['id'] in runtime['decisions'] or decision['id'] in state.executions:
        raise ValueError('decision already issued')
    runtime['decisions'][decision['id']] = dict(decision=deepcopy(bound), page_key=_digest(page),
        kind=commitment_kind(decision['operation'], action, page, assessment=assessment, key=decision.get('key')),
        effect=deepcopy(effect))
    return bound


def dispatch_once(state, journal: Journal, decision: dict, page: dict, *, act, is_fresh, interrupted,
                  proposal: dict | None = None, challenge_id: str | None = None) -> dict:
    """Durably claim one decision before input; return a receipt, never observe/retry."""
    from .browser import StalePage
    from .rules import secret_field
    runtime = _runtime(state)
    with runtime['lock']:
        eid = decision.get('id')
        issued = runtime['decisions'].get(eid)
        revision = state.revision
        if (not issued or issued['decision'] != decision or eid in state.executions
                or decision['revision'] != revision or issued['page_key'] != _digest(page)
                or decision['observation_id'] != next(reversed(state.observations), None)
                or state.status not in {'running', 'waiting_user'} or interrupted() or not is_fresh()
                or issued['kind'] == 'unresolved' or runtime.get('journal_failed')):
            raise ValueError('decision no longer executable or needs assessment')
        records = journal.records()
        if any(row.get('execution_id') == eid for row in records):
            raise ValueError('decision already consumed')
        if any(row.get('task_id') != state.task_id for row in records):
            raise ValueError('journal belongs to another task')
        observation = state.observations[decision['observation_id']]
        action = next(a for a in page['actions'] if a['id'] == decision['choice'])
        if decision['operation'] == 'TYPE_TEXT' and secret_field(action) and challenge_id is None:
            raise ValueError('secret input requires a current scoped challenge')
        challenge = {}
        if challenge_id is not None:
            challenge = state.challenges.get(challenge_id, {})
            if (challenge.get('revision') != revision or challenge.get('origin') != observation['origin']
                    or challenge.get('status') not in {
                        'challenge', 'issued', 'answer_ready', 'awaiting_answer', 'pending'}
                    or challenge.get('expires_at', float('inf')) is not None
                    and challenge.get('expires_at', float('inf')) <= time.time()
                    or challenge.get('observation_id') != decision['observation_id']
                    or not all(challenge.get(k) for k in ('purpose', 'field_role', 'question_id'))
                    or decision['choice'] not in challenge.get('field_ids', [])):
                raise ValueError('input challenge scope is stale or mismatched')
        if challenge_id is not None and any(x.get('challenge_id') == challenge_id
                and x.get('status') in {'pending', 'unknown', 'executed'} for x in state.executions.values()):
            raise ValueError('challenge input already submitted; observe its outcome')
        challenge = deepcopy(challenge)
        parent_id = challenge.get('commitment_execution_id')
        parent = state.executions.get(parent_id, {})
        # An observed payment challenge can finish an executed, approved transaction;
        # it cannot retry an unknown dispatch or start a second commitment.
        payment_followup = (issued['kind'] == 'authentication' and challenge.get('purpose') == 'payment'
                            and parent.get('commitment') is True and parent.get('status') == 'executed'
                            and parent.get('revision') == revision and bool(parent.get('approval_id'))
                            and challenge.get('proposal_key') == parent.get('proposal_key')
                            and (proposal is None or proposal_key(proposal) == parent['proposal_key']))
        if challenge.get('purpose') == 'payment' and not payment_followup:
            raise ValueError('payment challenge is not bound to the approved transaction')
        unresolved = journal.interrupted()
        pending = [x for x in state.executions.values() if x.get('status') in {'pending', 'unknown'}
                   or x.get('commitment') and x.get('status') == 'executed']
        if any(not payment_followup or x.get('execution_id') != parent_id or x.get('phase') != 'executed'
               for x in unresolved + pending):
            raise ValueError('unresolved execution blocks further mutations')
        commitment = issued['kind'] == 'commitment'
        effect = issued['effect']
        if commitment and (not effect or proposal is None
                or effect['proposal_key'] != proposal_key(proposal)
                or effect['effect_key'] != (state.approval or {}).get('effect_key')):
            raise ValueError('commitment differs from the approved observed effect')
        approval_id = consume_approval(state, proposal) if commitment and proposal is not None else None
        if commitment and approval_id is None:
            raise ValueError('commitment needs exact current approval')
        record = dict(execution_id=eid, phase='dispatching', task_id=state.task_id, revision=revision,
                      observation_id=decision['observation_id'], operation=decision['operation'],
                      choice=decision['choice'], origin=observation['origin'], challenge_id=challenge_id,
                      commitment_execution_id=parent_id,
                      flow_id=challenge.get('flow_id'), purpose=challenge.get('purpose'),
                      field_role=challenge.get('field_role'), question_id=challenge.get('question_id'),
                      commitment=commitment, proposal_key=proposal_key(proposal) if commitment else None,
                      effect_key=effect['effect_key'] if commitment else None,
                      approval_id=approval_id, account_key=state.progress.get('account_reservation_key'),
                      monotonic=time.monotonic(), wall_time=time.time(),
                      summary={'objective': state.objective, 'requirements': state.requirements})
        if commitment and not record['account_key']:
            raise ValueError('commitment requires code-owned account reservation key')
        # Mark consumed even on a partially persisted intent; it must never be replayed.
        state.executions[eid] = {**record, 'status': 'pending'}
        try:
            journal.append(record)
        except Exception:
            runtime['journal_failed'] = True
            state.executions[eid]['status'] = 'not_dispatched'
            raise
        status, result = 'not_dispatched', {}
        try:
            if (is_fresh() and state.revision == revision and state.status in {'running', 'waiting_user'}
                    and (not payment_followup or parent.get('status') == 'executed'
                         and parent.get('proposal_key') == challenge.get('proposal_key'))
                    and (not commitment or _current_review(state, proposal)
                         and proposal_key(proposal) == record['proposal_key'])
                    and (challenge_id is None or challenge == state.challenges.get(challenge_id)
                         and (challenge.get('expires_at') is None or challenge['expires_at'] > time.time()))
                    and not interrupted()):
                result = act()
                status = 'executed'
        except StalePage:
            status = 'not_dispatched'
        except Exception:
            status = 'unknown'
        try:
            result = journal.redact(result)
        except Exception:
            status, result = 'unknown', {}
            runtime['journal_failed'] = True
        receipt = {**record, 'phase': status, 'status': status, 'result': result,
                   'monotonic': time.monotonic(), 'wall_time': time.time()}
        state.executions[eid] = receipt
        try:
            journal.append(receipt)
        except Exception:
            runtime['journal_failed'] = True
            receipt['status'] = receipt['phase'] = 'unknown'
        return deepcopy(receipt)
