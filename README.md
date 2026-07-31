<div align="center">

# 📰 MyNews Bot

**Telegram-бот для автоматического парсинга, перевода и публикации IT-новостей**

[![Python](https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)

</div>

---

## 🔥 Возможности

- 📡 **Автоматический парсинг** RSS-лент (Habr, Xakep и другие)
- 🤖 **AI-суммаризация** через OpenRouter (Gemini / любая модель)
- 🌐 **Бесплатный перевод** на русский (Google Translate / DeepL)
- 📝 **Telegram HTML** — жирный, курсив, код, цитаты
- 👍👎 **Голосование** — лайки и дизлайки на каждую новость
- 📖 **Подробнее** — AI-саммари по клику с тоглом "Оригинал"
- 📅 **Автопостинг** — запланированная публикация в канал
- 🛡️ **Fallback** — при ошибке AI показывает оригинальный текст
- 🧹 **Анонимайзер файлов** — удаление метаданных (EXIF) и рандомное имя

---

## 📸 Скриншоты

<!-- Замените пути на реальные скриншоты -->

<table>
  <tr>
    <td align="center"><b>🏠 Главная</b></td>
    <td align="center"><b>📄 Лента новостей</b></td>
    <td align="center"><b>📅 Автопостинг</b></td>
  </tr>
  <tr>
    <td><img src="screenshots/mainpage.avif" width="250"></td>
    <td><video src="https://github.com/user-attachments/assets/c8f2b98e-5c82-481a-8251-88b7fd5512dd" width="250" autoplay loop muted playsinline></video></td>
    <td><img src="screenshots/autopublic.avif" width="250"></td>
  </tr>
  <tr>
    <td align="center"><b>⏰ Выбор времени</b></td>
    <td align="center"><b>📆 Календарь</b></td>
    <td align="center"><b>🧹 Анонимайзер</b></td>
  </tr>
  <tr>
    <td><img src="screenshots/hour.avif" width="250"></td>
    <td><img src="screenshots/calendar.avif" width="250"></td>
    <td><img src="screenshots/clearmetadata.avif" width="250"></td>
  </tr>
</table>

---

## 🚀 Быстрый старт

### 🐳 Docker (рекомендуется)

```bash
# Клонируйте репозиторий
git clone https://github.com/your-username/mynews.git
cd mynews

# Настройте переменные окружения приложения (BOT_TOKEN, OPENROUTER_API_KEY и т.д.)
cp src/.env.example src/.env
nano src/.env
```

Параметры БД (`DB_USER`, `DB_PASSWORD`, `DB_NAME`) в `src/.env` должны совпадать
с сервисом `db` из `docker-compose.yml` (по умолчанию: `postgres` / `db_password` /
`news_db`). Для подстановки переменных `docker compose` читает корневой `.env` —
укажите там те же значения (и необязательный `SSD_MOUNT`):

```env
# .env — корень проекта
DB_PASSWORD=db_password
DB_NAME=news_db
SSD_MOUNT=/mnt/ssd   # необязательно
```

```bash
# Соберите образ и запустите контейнеры (db + app)
docker compose up -d

# Дождитесь готовности БД и примените миграции
docker compose exec app alembic upgrade head

# Полезные команды
docker compose logs -f app    # логи бота
docker compose down           # остановить контейнеры
docker compose up -d --build  # пересобрать образ после изменений
```

> 💡 Приложение работает с `network_mode: host`, поэтому PostgreSQL и прокси
> должны быть доступны на хосте (`DB_HOST=localhost` из `.env.example` подходит).
> Контейнер `app` перезапускается автоматически, пока БД не будет готова.
> Если меняете `DB_PASSWORD`/`DB_NAME` после первого запуска — пересоздайте том БД:
> `docker compose down -v` (⚠️ удалит данные!), затем `docker compose up -d`.

### 💻 Локальная установка

```bash
cd src
pip install uv
uv sync
alembic upgrade head
python main.py
```

---

## ⚙️ Конфигурация

| Переменная | Описание | По умолчанию |
|-----------|----------|--------------|
| `BOT_TOKEN` | 🔑 Токен Telegram бота | — |
| `GROUP_ID` | 📢 ID группы для публикации | — |
| `CHANNEL_ID` | 📺 ID канала для автопостинга | — |
| `OPENROUTER_API_KEY` | 🤖 Ключ OpenRouter | — |
| `PROXY_URL` | 🌐 SOCKS5 прокси | `socks5://127.0.0.1:2080` |
| `AI_LANGUAGE` | 🌍 Язык суммаризации | `Russian` |
| `AI_SUMMARY_MAX_CHARS` | 📏 Макс. длина саммари | `2500` |

> 📋 Полный список в [`.env.example`](src/.env.example)

---

## 🌐 Прокси

Все запросы (Telegram, парсер, перевод) проходят через SOCKS5 прокси:

```env
PROXY_URL=socks5://127.0.0.1:2080
```

**Docker:** используется `network_mode: host` — прокси должен быть доступен на хосте.

---

## 🗄️ База данных

PostgreSQL автоматически разворачивается в Docker. Для хранения данных на SSD
укажите в корневом `.env`:

```env
SSD_MOUNT=/mnt/ssd
```

Каталог PostgreSQL монтируется в `/var/lib/postgresql`, как требуется
официальным Docker-образом PostgreSQL 18+. Файлы кластера PostgreSQL 18
сохраняются в `/mnt/ssd/postgres/18/docker/`.

---

## 🏗️ Структура

```
mynews/
├── src/
│   ├── ai/           🤖 AI (суммаризация, извлечение текста)
│   ├── bot/          🤖 Бот (хендлеры, клавиатуры, сервисы)
│   ├── core/         ⚙️ Конфигурация, БД, планировщик
│   ├── parser/       📡 RSS-парсеры
│   ├── utils/        🔧 Утилиты (перевод, календарь, инлайн)
│   ├── api/          🌐 FastAPI (вебхуки)
│   └── migrations/   🗃️ Миграции
├── Dockerfile        🐳 Multi-stage build
├── docker-compose.yml
└── README.md
```

---

## 🛠️ Стек

| Компонент | Технология |
|-----------|-----------|
| 🐍 Язык | Python 3.14 |
| 🤖 Бот | aiogram 3 |
| 🗄️ БД | PostgreSQL 18 |
| 🔄 ORM | SQLAlchemy + Alembic |
| 📡 Парсинг | feedparser + trafilatura |
| 🌐 Перевод | deep_translator |
| 🧠 AI | OpenRouter |
| 🧹 Метаданные | Pillow |
| 🐳 Контейнеры | Docker Compose |

---

## 📄 Лицензия

[MIT](LICENSE)

---

<div align="center">

**⭐ Если проект понравился — поставьте звёздочку!**

</div>
