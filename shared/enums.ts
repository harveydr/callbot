// Generated from shared/enums.py. Do not edit by hand.

export const SCENARIO_CODE = ["notification", "callback_survey", "telesales", "collection"] as const;
export type SCENARIO_CODE = (typeof SCENARIO_CODE)[number];

export const TENANT_STATUS = ["pending", "active", "suspended", "closed"] as const;
export type TENANT_STATUS = (typeof TENANT_STATUS)[number];

export const ROLE = ["platform_admin", "tenant_admin", "operator", "agent", "auditor"] as const;
export type ROLE = (typeof ROLE)[number];

export const USER_STATUS = ["active", "locked", "disabled"] as const;
export type USER_STATUS = (typeof USER_STATUS)[number];

export const QUALIFICATION_DOC_TYPE = ["business_license", "telco_value_added_license", "callcenter_license", "number_certificate", "consent_policy", "script_filing", "other"] as const;
export type QUALIFICATION_DOC_TYPE = (typeof QUALIFICATION_DOC_TYPE)[number];

export const QUALIFICATION_STATUS = ["pending", "approved", "rejected", "expired"] as const;
export type QUALIFICATION_STATUS = (typeof QUALIFICATION_STATUS)[number];

export const TRUNK_MODE = ["byoc", "managed"] as const;
export type TRUNK_MODE = (typeof TRUNK_MODE)[number];

export const TRUNK_TRANSPORT = ["udp", "tcp", "tls"] as const;
export type TRUNK_TRANSPORT = (typeof TRUNK_TRANSPORT)[number];

export const TRUNK_STATUS = ["inactive", "active", "blocked"] as const;
export type TRUNK_STATUS = (typeof TRUNK_STATUS)[number];

export const CALLER_NUMBER_STATUS = ["active", "cooling", "blocked"] as const;
export type CALLER_NUMBER_STATUS = (typeof CALLER_NUMBER_STATUS)[number];

export const CONSENT_SOURCE = ["form", "app", "contract", "sms_confirm", "ivr_confirm", "import_with_proof", "offline_sign"] as const;
export type CONSENT_SOURCE = (typeof CONSENT_SOURCE)[number];

export const CAMPAIGN_STATUS = ["draft", "pending_review", "approved", "running", "paused", "done", "aborted"] as const;
export type CAMPAIGN_STATUS = (typeof CAMPAIGN_STATUS)[number];

export const CALL_STATUS = ["queued", "dialing", "ringing", "answered", "talking", "interrupted", "transferring", "transfer_failed", "completed", "no_answer", "busy", "rejected", "failed", "blocked_compliance", "cancelled"] as const;
export type CALL_STATUS = (typeof CALL_STATUS)[number];

export const INTENT_GRADE = ["A", "B", "C", "D", "NA"] as const;
export type INTENT_GRADE = (typeof INTENT_GRADE)[number];

export const INTENT = ["agree", "refuse", "hesitate", "inquiry", "complaint", "invalid"] as const;
export type INTENT = (typeof INTENT)[number];

export const DND_SOURCE = ["user_said_no", "complaint", "carrier_report", "operator_manual", "global_12321"] as const;
export type DND_SOURCE = (typeof DND_SOURCE)[number];

export const DND_SCOPE = ["tenant", "platform"] as const;
export type DND_SCOPE = (typeof DND_SCOPE)[number];

export const DIAL_CLASS = ["dialable", "no_consent", "dnd_hit", "invalid"] as const;
export type DIAL_CLASS = (typeof DIAL_CLASS)[number];

export const IMPORT_STATUS = ["idle", "running", "done", "failed"] as const;
export type IMPORT_STATUS = (typeof IMPORT_STATUS)[number];

export const TEMPLATE_STATUS = ["draft", "pending_review", "approved", "rejected"] as const;
export type TEMPLATE_STATUS = (typeof TEMPLATE_STATUS)[number];
