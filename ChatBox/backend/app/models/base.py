from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData()


UUIDPrimaryKey = Annotated[
    UUID,
    mapped_column(primary_key=True, default=uuid4),
]
CreatedAt = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False),
]
