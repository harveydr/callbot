"""ORM models. Queries use explicit options; relationships are not lazy-loaded."""

from app.models import extra as extra
from app.models.tables import (
    AuditLog,
    Call,
    CallEvent,
    CallerNumber,
    Campaign,
    ConsentRecord,
    Contact,
    ContactList,
    DndEntry,
    PlatformDnd,
    Qualification,
    ScenarioTemplate,
    Task,
    Tenant,
    Trunk,
    User,
)

__all__ = [
    "AuditLog",
    "Call",
    "CallEvent",
    "CallerNumber",
    "Campaign",
    "ConsentRecord",
    "Contact",
    "ContactList",
    "DndEntry",
    "PlatformDnd",
    "Qualification",
    "ScenarioTemplate",
    "Task",
    "Tenant",
    "Trunk",
    "User",
    "extra",
]
