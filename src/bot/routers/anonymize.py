import io
import random
import string
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils.inline import delete_inline

anrtr = Router()


class AnonymizeState(StatesGroup):
    waiting_file = State()


def _random_filename(original: str) -> str:
    ext = original.rsplit(".", 1)[-1] if "." in original else "bin"
    name = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{name}.{ext}"


def _strip_image_metadata(data: bytes, filename: str) -> tuple[bytes, str]:
    from PIL import Image

    img = Image.open(io.BytesIO(data))

    cleaned = img.copy()
    cleaned.info.clear()

    buf = io.BytesIO()
    fmt = img.format or "PNG"
    if fmt == "JPEG":
        cleaned.save(buf, format="JPEG", exif=b"")
    elif fmt == "PNG":
        cleaned.save(buf, format="PNG")
    elif fmt == "WEBP":
        cleaned.save(buf, format="WEBP")
    elif fmt == "GIF":
        cleaned.save(buf, format="GIF")
    elif fmt == "BMP":
        cleaned.save(buf, format="BMP")
    else:
        cleaned.save(buf, format=fmt)

    buf.seek(0)
    return buf.read(), _random_filename(filename)


def process_file(data: bytes, filename: str) -> tuple[bytes, str]:
    lower = filename.lower()
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff")
    if any(lower.endswith(e) for e in image_exts):
        try:
            return _strip_image_metadata(data, filename)
        except Exception:
            pass

    return data, _random_filename(filename)


@anrtr.callback_query(F.data == "anonymize")
async def start_anonymize(clck: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="cancel_anonymize", icon_custom_emoji_id="5260748017434130156")
    kb.adjust(1)

    await state.set_state(AnonymizeState.waiting_file)
    await clck.message.edit_text(
        text=(
            "<b>Отправьте файл или фото</b>\n\n"
            "Я удалю метаданные (EXIF и т.д.) "
            "и верну файл с рандомным именем."
        ),
        reply_markup=kb.as_markup(),
    )
    await state.update_data(data={"previn": clck.message.message_id})
    await clck.answer()


@anrtr.callback_query(F.data == "cancel_anonymize")
async def cancel_anonymize(clck: CallbackQuery, state: FSMContext):
    await delete_inline(state, clck)
    await state.clear()
    await clck.message.edit_text("Отменено.")
    await clck.answer()


@anrtr.message(AnonymizeState.waiting_file, F.document)
async def handle_file(msg: Message, state: FSMContext, bot: Bot):
    doc = msg.document
    if not doc or not doc.file_name:
        await msg.answer("Не удалось определить имя файла.")
        return

    if doc.file_size and doc.file_size > 50 * 1024 * 1024:
        await msg.answer("Файл слишком большой (макс. 50 МБ).")
        return

    await delete_inline(state, msg)
    await state.clear()

    status = await msg.answer("⏳ Обрабатываю файл...")

    file_info = await bot.get_file(doc.file_id)
    raw = await bot.download_file(file_info.file_path)
    data = raw.read()

    result, new_name = process_file(data, doc.file_name)

    await bot.send_document(
        chat_id=msg.chat.id,
        document=BufferedInputFile(result, filename=new_name),
        caption=f"Готово: <code>{new_name}</code>",
    )
    await bot.delete_message(chat_id=status.chat.id, message_id=status.message_id)


@anrtr.message(AnonymizeState.waiting_file, F.photo)
async def handle_photo(msg: Message, state: FSMContext, bot: Bot):
    photo = msg.photo[-1]

    await delete_inline(state, msg)
    await state.clear()

    status = await msg.answer("⏳ Обрабатываю фото...")

    file_info = await bot.get_file(photo.file_id)
    raw = await bot.download_file(file_info.file_path)
    data = raw.read()

    result, new_name = _strip_image_metadata(data, "photo.jpg")

    await bot.send_document(
        chat_id=msg.chat.id,
        document=BufferedInputFile(result, filename=new_name),
        caption=f"Готово: <code>{new_name}</code>",
    )
    await bot.delete_message(chat_id=status.chat.id, message_id=status.message_id)
