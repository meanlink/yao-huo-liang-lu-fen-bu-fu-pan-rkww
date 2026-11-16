from sqlalchemy import String, JSON, Double, Integer, Boolean, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class BlindTrial(Base):
    """一次工艺盲评：同一条窑线上登记两个配方组，锁定后占用窑次。"""
    __tablename__ = 'blind_trials'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    line: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default='registered')
    version: Mapped[int] = mapped_column(Integer, default=0)
    seal_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seal_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    locked_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    sealed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    unblinded_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    terminated_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class TrialGroup(Base):
    __tablename__ = 'trial_groups'
    __table_args__ = (UniqueConstraint('trial_id', 'position', name='uq_trial_group_position'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey('blind_trials.id'))
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    recipe: Mapped[str] = mapped_column(String(500))


class TrialUnit(Base):
    __tablename__ = 'trial_units'
    __table_args__ = (
        UniqueConstraint('trial_id', 'batch', name='uq_trial_unit_batch'),
        UniqueConstraint('blind_code', name='uq_trial_unit_code'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey('blind_trials.id'))
    group_id: Mapped[int] = mapped_column(ForeignKey('trial_groups.id'))
    batch: Mapped[str] = mapped_column(String(60))
    blind_code: Mapped[str] = mapped_column(String(30))


class BatchClaim(Base):
    """在途占用：partial unique index 保证同一窑次全局只有一个活跃试验。"""
    __tablename__ = 'batch_claims'
    __table_args__ = (
        Index('uq_batch_claim_active', 'batch', unique=True,
              postgresql_where=text('active'), sqlite_where=text('active = 1')),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey('blind_trials.id'))
    batch: Mapped[str] = mapped_column(String(60))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    claimed_at: Mapped[float] = mapped_column(Double)
    released_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class Inspection(Base):
    __tablename__ = 'blind_inspections'
    __table_args__ = (UniqueConstraint('unit_id', name='uq_inspection_unit'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey('blind_trials.id'))
    unit_id: Mapped[int] = mapped_column(ForeignKey('trial_units.id'))
    blind_code: Mapped[str] = mapped_column(String(30))
    fired: Mapped[int] = mapped_column(Integer)
    passed: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    updated_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class BlindReview(Base):
    __tablename__ = 'blind_reviews'
    __table_args__ = (UniqueConstraint('trial_id', name='uq_blind_review_trial'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey('blind_trials.id'))
    status: Mapped[str] = mapped_column(String(20), default='queued')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_version: Mapped[int] = mapped_column(Integer, default=0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    approved_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_at: Mapped[float] = mapped_column(Double)
    completed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
