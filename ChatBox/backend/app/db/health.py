from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .session import engine


async def check_database_health() -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True