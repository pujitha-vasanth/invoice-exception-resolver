import pytest

from resolver import config, db, guardrails, workflow


def record(conn, invoice_id, action, exception_type="price_mismatch"):
    return workflow.record_resolution(conn, invoice_id, exception_type, action, "test summary", ["AP-1"], "test")


def test_payment_release_always_needs_a_human(conn):
    # INV-1002 is a small, in-tolerance variance. Still a payment, so still a human.
    result = record(conn, "INV-1002", "approve_payment")
    assert result["status"] == "AWAITING_APPROVAL"
    assert result["payment_amount"] == 38600.0
    assert db.get_invoice(conn, "INV-1002")["status"] == "AWAITING_APPROVAL"


def test_small_non_payment_action_is_within_agent_authority(conn):
    result = record(conn, "INV-1003", "escalate_to_buyer")
    assert result["status"] == "AUTO_RESOLVED"
    assert db.get_invoice(conn, "INV-1003")["status"] == "ESCALATED"


def test_large_exposure_needs_a_human_even_for_a_hold(conn):
    result = record(conn, "INV-1006", "hold", "quantity_mismatch")
    assert result["exposure_amount"] == 97500.0
    assert result["exposure_amount"] >= config.APPROVAL_THRESHOLD
    assert result["status"] == "AWAITING_APPROVAL"


def test_exposure_comes_from_data_not_from_the_agent(conn):
    # There is no way to pass an amount in. The exposure is always computed from the match.
    result = record(conn, "INV-1004", "escalate_to_buyer")
    assert result["exposure_amount"] == 50000.0
    assert result["status"] == "AWAITING_APPROVAL"


def test_cannot_pay_for_goods_not_received(conn):
    with pytest.raises(guardrails.GuardrailViolation, match="not been received"):
        record(conn, "INV-1005", "approve_payment", "quantity_mismatch")
    assert db.get_invoice(conn, "INV-1005")["status"] == "EXCEPTION"


def test_short_pay_pays_only_for_received_quantity(conn):
    result = record(conn, "INV-1005", "short_pay", "quantity_mismatch")
    assert result["payment_amount"] == 270 * 950.00
    assert result["status"] == "AWAITING_APPROVAL"


def test_unknown_action_is_rejected(conn):
    with pytest.raises(guardrails.GuardrailViolation, match="Unknown action"):
        record(conn, "INV-1003", "release_funds_now")


def test_cannot_resolve_the_same_invoice_twice(conn):
    record(conn, "INV-1003", "escalate_to_buyer")
    with pytest.raises(guardrails.GuardrailViolation, match="Only open exceptions"):
        record(conn, "INV-1003", "approve_payment")


def test_human_approval_moves_invoice_to_ready_to_pay(conn):
    result = record(conn, "INV-1002", "approve_payment")
    decision = workflow.decide(conn, result["resolution_id"], True, "Test Approver", "ok")
    assert decision["invoice_status"] == "READY_TO_PAY"
    events = [a["event"] for a in db.audit_trail(conn, "INV-1002")]
    assert "human_decision" in events


def test_human_rejection_sends_invoice_to_manual_review(conn):
    result = record(conn, "INV-1002", "approve_payment")
    decision = workflow.decide(conn, result["resolution_id"], False, "Test Approver", "check again")
    assert decision["invoice_status"] == "MANUAL_REVIEW"


def test_approval_needs_a_name(conn):
    result = record(conn, "INV-1002", "approve_payment")
    with pytest.raises(ValueError, match="approver name"):
        workflow.decide(conn, result["resolution_id"], True, "  ")
