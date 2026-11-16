"""SQLAlchemy ORM models, database initialization, and default seed records."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from app.config import get_settings


def utcnow() -> datetime:
    """Return timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    """Return a string UUID4 identifier."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy models."""


class User(Base):
    """Enterprise directory user for RBAC and session ownership."""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=new_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, default="employee")  # employee | support_agent | admin
    department = Column(String(128), nullable=False, default="Engineering")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")


class ChatSession(Base):
    """Conversational helpdesk session supporting AI resolution and human takeover."""

    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user_email = Column(String(255), nullable=False, index=True)
    title = Column(String(255), nullable=False, default="New IT Support Request")
    status = Column(
        String(32),
        nullable=False,
        default="active",  # active | ai_resolved | escalated | agent_active | closed
        index=True,
    )
    assigned_agent_email = Column(String(255), nullable=True)
    handoff_summary = Column(Text, nullable=True)
    deflected = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="sessions")
    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    """Individual message in a chat session with citations and tool actions."""

    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_role = Column(String(32), nullable=False)  # user | assistant | support_agent | system
    sender_email = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    pii_redacted = Column(Boolean, nullable=False, default=False)
    citations_json = Column(JSON, nullable=False, default=list)
    tool_calls_json = Column(JSON, nullable=False, default=list)
    intent = Column(String(64), nullable=True)
    latency_ms = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    session = relationship("ChatSession", back_populates="messages")


class Ticket(Base):
    """IT Support / Escalation / Provisioning Ticket."""

    __tablename__ = "tickets"

    id = Column(String(32), primary_key=True)
    session_id = Column(
        String(36), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requester_email = Column(String(255), nullable=False, index=True)
    requester_name = Column(String(255), nullable=False, default="Enterprise Employee")
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    category = Column(String(64), nullable=False, default="general_it")
    priority = Column(String(32), nullable=False, default="medium")  # low | medium | high | critical
    status = Column(
        String(32),
        nullable=False,
        default="open",  # open | in_progress | escalated | pending_approval | resolved | closed
        index=True,
    )
    assigned_agent_email = Column(String(255), nullable=True)
    ai_handoff_summary = Column(Text, nullable=True)
    external_system = Column(String(64), nullable=True, default="sandbox")
    external_ticket_ref = Column(String(64), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """Security, guardrail, RBAC, workflow, and admin audit trail."""

    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    event_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(32), nullable=False, default="info")  # info | warning | high | critical
    actor_email = Column(String(255), nullable=False, index=True)
    actor_role = Column(String(32), nullable=False)
    session_id = Column(String(36), nullable=True, index=True)
    action = Column(String(255), nullable=False)
    details_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class MetricRecord(Base):
    """Operational KPI telemetry: latency, deflection, MTTR, RAG faithfulness/relevance."""

    __tablename__ = "metrics"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(String(36), nullable=True, index=True)
    metric_type = Column(String(64), nullable=False, index=True)  # chat_turn | rag_eval | ticket_resolution
    intent = Column(String(64), nullable=True)
    latency_ms = Column(Float, nullable=False, default=0.0)
    faithfulness_score = Column(Float, nullable=True)
    relevance_score = Column(Float, nullable=True)
    deflected = Column(Boolean, nullable=True)
    escalated = Column(Boolean, nullable=True)
    mttr_minutes = Column(Float, nullable=True)
    tokens_used = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class KBDocument(Base):
    """Indexed knowledge base SOP document registry."""

    __tablename__ = "kb_documents"

    id = Column(String(64), primary_key=True)
    filename = Column(String(255), unique=True, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    doc_code = Column(String(64), nullable=False, default="SOP")
    file_type = Column(String(32), nullable=False, default="md")
    chunk_count = Column(Integer, nullable=False, default=0)
    content_hash = Column(String(64), nullable=False)
    source_path = Column(String(512), nullable=False)
    indexed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class WebhookDelivery(Base):
    """Log of outbound webhook dispatches and sandbox receiver responses."""

    __tablename__ = "webhook_deliveries"

    id = Column(String(36), primary_key=True, default=new_uuid)
    target_system = Column(String(64), nullable=False, index=True)  # jira | servicenow | n8n | okta_scim
    endpoint_url = Column(String(512), nullable=False)
    event_name = Column(String(128), nullable=False)
    payload_json = Column(JSON, nullable=False, default=dict)
    response_status = Column(Integer, nullable=False, default=200)
    response_body_json = Column(JSON, nullable=False, default=dict)
    delivery_mode = Column(String(32), nullable=False, default="sandbox")  # live | sandbox
    latency_ms = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


def _create_engine():
    settings = get_settings()
    db_url = settings.database_url
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # Ensure parent directory exists for SQLite file paths
        raw_path = db_url.replace("sqlite:///", "", 1)
        if raw_path and raw_path != ":memory:":
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)

    if db_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transactional SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create database tables and seed default enterprise RBAC personas."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_default_data(db)
    finally:
        db.close()


def seed_default_data(db: Session) -> None:
    """Ensure baseline enterprise users exist for Employee, Support Agent, and Admin roles."""
    default_users = [
        {
            "email": "alex.chen@acme-corp.com",
            "full_name": "Alex Chen",
            "role": "employee",
            "department": "Product Engineering",
        },
        {
            "email": "maya.patel@acme-corp.com",
            "full_name": "Maya Patel",
            "role": "support_agent",
            "department": "IT Service Desk",
        },
        {
            "email": "jordan.taylor@acme-corp.com",
            "full_name": "Jordan Taylor",
            "role": "admin",
            "department": "Enterprise IT & Security",
        },
    ]

    for user_data in default_users:
        exists = db.query(User).filter(User.email == user_data["email"]).first()
        if not exists:
            db.add(User(**user_data))

    db.commit()

    # Seed initial historical baseline metrics & a sample resolved/open ticket ONLY if DB is completely empty
    if db.query(Ticket).count() == 0 and db.query(MetricRecord).count() == 0:
        now = utcnow()
        sample_ticket_1 = Ticket(
            id="INC-1001",
            requester_email="alex.chen@acme-corp.com",
            requester_name="Alex Chen",
            title="VPN-412 Posture Compliance Failure After macOS Update",
            description="GlobalProtect drops with VPN-412 even after Company Portal sync.",
            category="network_vpn",
            priority="high",
            status="escalated",
            ai_handoff_summary=(
                "User encountered VPN-412 HIP compliance check failure on macOS. "
                "L1 SOP steps (MDM sync and reboot) were advised per SOP-IT-001 §3.4; "
                "escalated to Tier-2 Endpoint Security to inspect CrowdStrike Falcon daemon."
            ),
            external_system="sandbox",
            external_ticket_ref="ITSD-1001",
            created_at=now - timedelta(hours=3, minutes=20),
            updated_at=now - timedelta(hours=3, minutes=15),
        )
        sample_ticket_2 = Ticket(
            id="REQ-1002",
            requester_email="alex.chen@acme-corp.com",
            requester_name="Alex Chen",
            title="Figma Organization Editor Seat Request",
            description="Need Figma Editor access for Q2 design architecture review.",
            category="software_access",
            priority="medium",
            status="resolved",
            assigned_agent_email="maya.patel@acme-corp.com",
            external_system="sandbox",
            external_ticket_ref="ITSD-1002",
            resolution_notes="Manager approved and SCIM provisioned Figma Organization Editor seat.",
            created_at=now - timedelta(hours=6),
            updated_at=now - timedelta(hours=5, minutes=42),
            resolved_at=now - timedelta(hours=5, minutes=42),
        )
        db.add_all([sample_ticket_1, sample_ticket_2])

        baseline_metrics = [
            MetricRecord(
                metric_type="chat_turn",
                intent="rag_qa",
                latency_ms=412.5,
                faithfulness_score=0.95,
                relevance_score=0.92,
                deflected=True,
                escalated=False,
                tokens_used=640,
                created_at=now - timedelta(hours=5),
            ),
            MetricRecord(
                metric_type="chat_turn",
                intent="tool_password_reset",
                latency_ms=528.0,
                faithfulness_score=0.97,
                relevance_score=0.94,
                deflected=True,
                escalated=False,
                tokens_used=510,
                created_at=now - timedelta(hours=4),
            ),
            MetricRecord(
                metric_type="chat_turn",
                intent="escalate",
                latency_ms=485.0,
                faithfulness_score=0.91,
                relevance_score=0.89,
                deflected=False,
                escalated=True,
                tokens_used=720,
                created_at=now - timedelta(hours=3),
            ),
            MetricRecord(
                metric_type="ticket_resolution",
                intent="tool_software_access",
                latency_ms=320.0,
                mttr_minutes=18.0,
                deflected=False,
                escalated=False,
                created_at=now - timedelta(hours=5, minutes=42),
            ),
        ]
        db.add_all(baseline_metrics)
        db.add(
            AuditLog(
                event_type="system_startup",
                severity="info",
                actor_email="system@acme-corp.com",
                actor_role="admin",
                action="Initialized AI-HAS baseline enterprise directory and telemetry",
                details_json={"seeded_users": 3, "seeded_tickets": 2},
                created_at=now - timedelta(hours=6),
            )
        )
        db.commit()
