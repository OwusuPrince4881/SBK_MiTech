from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.domain.base import Base


class SampleImage(Base):
    __tablename__ = "sample_images"

    image_id: Mapped[UUID] = mapped_column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    id = synonym("image_id")
    sample_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("samples.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    relative_path: Mapped[str] = mapped_column(
        "storage_path",
        String(512),
        nullable=False,
        unique=True,
    )
    storage_path = synonym("relative_path")
    sample: Mapped["Sample"] = relationship(back_populates="images")
