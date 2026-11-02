from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base


class Sample(Base):
    __tablename__ = "samples"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    sample_name: Mapped[str] = mapped_column(Text, nullable=False)
    material_description: Mapped[str] = mapped_column(Text, nullable=False)
    collection_location: Mapped[str] = mapped_column(Text, nullable=False)
    intended_application: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    images: Mapped[list["SampleImage"]] = relationship(
        back_populates="sample",
        cascade="all, delete-orphan",
    )
