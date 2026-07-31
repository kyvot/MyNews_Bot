from aiogram.fsm.state import State, StatesGroup


class AutopostST(StatesGroup):
    waiting_post = State()
