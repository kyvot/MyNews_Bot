from fastapi import APIRouter
from api.v1 import onertr

main_router = APIRouter()
main_router.include_router(onertr)
