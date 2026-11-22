"""Canonical enums. Frontend constants are generated from this module."""

from __future__ import annotations

SCENARIO_CODES = (
    "notification",
    "callback_survey",
    "telesales",
    "collection",
)
TENANT_STATUSES = ("pending", "active", "suspended", "closed")
ROLES = ("platform_admin", "tenant_admin", "operator", "agent", "auditor")
USER_STATUSES = ("active", "locked", "disabled")
QUALIFICATION_DOC_TYPES = (
    "business_license",
    "telco_value_added_license",
    "callcenter_license",
    "number_certificate",
    "consent_policy",
    "script_filing",
    "other",
)
QUALIFICATION_STATUSES = ("pending", "approved", "rejected", "expired")
TRUNK_MODES = ("byoc", "managed")
TRUNK_TRANSPORTS = ("udp", "tcp", "tls")
TRUNK_STATUSES = ("inactive", "active", "blocked")
CALLER_NUMBER_STATUSES = ("active", "cooling", "blocked")
CONSENT_SOURCES = (
    "form",
    "app",
    "contract",
    "sms_confirm",
    "ivr_confirm",
    "import_with_proof",
    "offline_sign",
)
CAMPAIGN_STATUSES = (
    "draft",
    "pending_review",
    "approved",
    "running",
    "paused",
    "done",
    "aborted",
)
CALL_STATUSES = (
    "queued",
    "dialing",
    "ringing",
    "answered",
    "talking",
    "interrupted",
    "transferring",
    "transfer_failed",
    "completed",
    "no_answer",
    "busy",
    "rejected",
    "failed",
    "blocked_compliance",
    "cancelled",
)
INTENT_GRADES = ("A", "B", "C", "D", "NA")
INTENTS = ("agree", "refuse", "hesitate", "inquiry", "complaint", "invalid")
DND_SOURCES = (
    "user_said_no",
    "complaint",
    "carrier_report",
    "operator_manual",
    "global_12321",
)
DND_SCOPES = ("tenant", "platform")
DIAL_CLASSES = ("dialable", "no_consent", "dnd_hit", "invalid")
IMPORT_STATUSES = ("idle", "running", "done", "failed")
TEMPLATE_STATUSES = ("draft", "pending_review", "approved", "rejected")

ENUMS: dict[str, tuple[str, ...]] = {
    "scenario_code": SCENARIO_CODES,
    "tenant_status": TENANT_STATUSES,
    "role": ROLES,
    "user_status": USER_STATUSES,
    "qualification_doc_type": QUALIFICATION_DOC_TYPES,
    "qualification_status": QUALIFICATION_STATUSES,
    "trunk_mode": TRUNK_MODES,
    "trunk_transport": TRUNK_TRANSPORTS,
    "trunk_status": TRUNK_STATUSES,
    "caller_number_status": CALLER_NUMBER_STATUSES,
    "consent_source": CONSENT_SOURCES,
    "campaign_status": CAMPAIGN_STATUSES,
    "call_status": CALL_STATUSES,
    "intent_grade": INTENT_GRADES,
    "intent": INTENTS,
    "dnd_source": DND_SOURCES,
    "dnd_scope": DND_SCOPES,
    "dial_class": DIAL_CLASSES,
    "import_status": IMPORT_STATUSES,
    "template_status": TEMPLATE_STATUSES,
}


def render_typescript() -> str:
    lines = [
        "// Generated from shared/enums.py. Do not edit by hand.",
        "",
    ]
    for name, values in ENUMS.items():
        const = name.upper()
        body = ", ".join(f'"{value}"' for value in values)
        lines.append(f"export const {const} = [{body}] as const;")
        lines.append(f"export type {const} = (typeof {const})[number];")
        lines.append("")
    return "\n".join(lines)
