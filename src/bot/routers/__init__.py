from aiogram import Router
from .hello import start_rtr
from .admin import adminrtr
from .autopost import autrtr
from .news import newsrtr
from .system import sysrtr
from .anonymize import anrtr

main_router = Router()
main_router.include_router(sysrtr)
main_router.include_router(start_rtr)
main_router.include_router(adminrtr)
main_router.include_router(newsrtr)
main_router.include_router(autrtr)
main_router.include_router(anrtr)
