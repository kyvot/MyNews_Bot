from fastapi import APIRouter
from .system import sysrtr

onertr = APIRouter()
onertr.include_router(sysrtr)
