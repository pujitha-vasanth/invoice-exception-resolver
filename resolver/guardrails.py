"""Hard guardrails, written in plain code. The LLM cannot talk its way past these.

Rule 1: any action that releases money always needs a human approver.
Rule 2: any action where the money at stake is at or above APPROVAL_THRESHOLD needs a human approver.
Rule 3: the money at stake is calculated here from the data, never taken from the agent.
Rule 4: the agent cannot approve paying for goods that were never received.
"""

from . import config

PAYMENT_ACTIONS = {"approve_payment", "short_pay"}
NON_PAYMENT_ACTIONS = {"hold", "escalate_to_buyer", "reject_duplicate"}
ALLOWED_ACTIONS = PAYMENT_ACTIONS | NON_PAYMENT_ACTIONS


class GuardrailViolation(ValueError):
    """Raised when an agent tries something the rules do not allow. The message goes back to the agent."""


def compute_exposure(analysis: dict, action: str) -> float:
    """How much money is at stake, worked out from the three-way match, not from the agent."""
    exposure = abs(analysis["price_variance_amount"]) + analysis["quantity_variance_amount"]
    if action == "reject_duplicate" or "possible_duplicate" in analysis["flags"]:
        # If this might be a duplicate, the whole invoice is at stake.
        exposure = max(exposure, analysis["invoice_total"])
    return round(exposure, 2)


def compute_payment_amount(analysis: dict, action: str) -> float:
    if action == "approve_payment":
        return round(analysis["invoice_total"], 2)
    if action == "short_pay":
        # Pay only for what was received, at no more than the PO price.
        total = 0.0
        for line in analysis["lines"]:
            if "po_unit_price" not in line:
                continue
            qty = min(line["invoiced_qty"], line["qty_available_to_bill"])
            total += qty * min(line["invoiced_unit_price"], line["po_unit_price"])
        return round(total, 2)
    return 0.0


def validate_action(analysis: dict, action: str) -> None:
    if action not in ALLOWED_ACTIONS:
        raise GuardrailViolation(f"Unknown action '{action}'. Allowed: {sorted(ALLOWED_ACTIONS)}")
    if action == "approve_payment" and analysis["quantity_variance_amount"] > 0:
        raise GuardrailViolation(
            "Blocked: approve_payment would pay for goods that have not been received. "
            "Use short_pay or hold instead."
        )
    if action == "short_pay" and analysis["quantity_variance_amount"] <= 0:
        raise GuardrailViolation("Blocked: short_pay only applies when the invoice bills more than was received.")


def approval_check(analysis: dict, action: str) -> tuple[bool, str, float, float]:
    """Returns (needs_human_approval, reason, exposure, payment_amount)."""
    validate_action(analysis, action)
    exposure = compute_exposure(analysis, action)
    payment = compute_payment_amount(analysis, action)
    reasons = []
    if action in PAYMENT_ACTIONS:
        reasons.append(f"releases a payment of {config.money(payment)}; every payment release needs a human approver")
    if exposure >= config.APPROVAL_THRESHOLD:
        reasons.append(
            f"amount at stake {config.money(exposure)} is at or above the approval threshold "
            f"of {config.money(config.APPROVAL_THRESHOLD)}"
        )
    return bool(reasons), "; ".join(reasons), exposure, payment
