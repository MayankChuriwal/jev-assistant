"""Small state fixtures shared by the reliability tests."""

from fakes import fake_page

from assistant.state import TaskState, cite, observe_state


def observed(text="Checkout", goal="Complete the requested task", url="https://shop.test/"):
    s = TaskState("t1", goal)
    page = fake_page(text=text, url=url)
    oid = observe_state(s, page)
    return s, page, oid


def evidence_for(s, oid, quote):
    return (cite(s, oid, quote),)
