from core.config import logger
from core.db.db import get_session
from core.db.models import Admins
from core.config import stg
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def create_default_admin(ses: AsyncSession):
    default_admin = Admins(
        user_id=stg.DEFAULT_ADMIN_ID,
        perm_id=0
    )
    ses.add(default_admin)
    await ses.commit()
    logger.info("Default admin created successfully!")
    return default_admin


async def get_admins(ses: AsyncSession):
    admins = (await ses.execute(select(Admins))).scalars().all()
    admins = set(admins) or None
    if not admins:
        logger.info("No admins found in the database. Creating the default admin.")
        default_admin = await create_default_admin(ses=ses)
        admins = {default_admin}
    return admins

