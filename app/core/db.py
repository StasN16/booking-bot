"""
The database engine, created once.

Each service used to build its own engine, so a single process opened four
connection pools against the same database. Supabase's pooler caps
connections per project, and four pools is a needless way to approach that
limit. Everything shares this one.
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
