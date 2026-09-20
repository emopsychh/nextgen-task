# NextGen Task

Кабинет агентства и клиентов для ведения проектов, задач, часов и отчётов.

- **Агентство** работает в веб-кабинете (логин/пароль)
- **Клиенты** входят логином/паролем через веб и/или открывают приложение из Битрикс24
- Источник правды — **Postgres**. Зеркало задач в агентский Битрикс и CRM-синк часов **выключены** по умолчанию
- Пакет часов по клиенту задаётся в UI агентства (без CRM Bitrix)

Стек: **React (Vite) + Django/DRF + Celery + PostgreSQL + Redis + Docker**

## Структура

```
backend/     Django API, Celery
frontend/    React SPA (agency + client)
docker-compose.yml / docker-compose.prod.yml
scripts/     ops (backup-db.sh)
```

## Быстрый старт (Docker)

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:5173  
- API / admin: http://localhost:8000  

Dev-вход без паролей: кнопки на LoginPage при `DEV_AUTH_BYPASS=1`.

**Продакшен:** см. [DEPLOY.md](DEPLOY.md).

## Вход

| Кто | Как |
|-----|-----|
| Агентство | Логин/пароль (`/api/auth/login/`). Пользователей выдаёт админ (Django admin → Bitrix users) |
| Клиент | То же через веб **или** OAuth из локального приложения Bitrix24 |
| Смена пароля | В сайдбаре «Сменить пароль» (нужен текущий пароль) |

Флаги в `.env` / `.env.production`:

```
BITRIX_AGENCY_TASK_SYNC=0   # не зеркалить задачи в агентский Bitrix
BITRIX_CRM_SYNC=0           # не тянуть сделки/часы из CRM
DEV_AUTH_BYPASS=0           # обязательно 0 в проде
```

## Локально без Docker

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\activate
pip install -r backend/requirements.txt
set DATABASE_URL=sqlite:///db.sqlite3
set CELERY_TASK_ALWAYS_EAGER=1
cd backend
python manage.py migrate
python manage.py runserver
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Бэкап БД (prod)

```bash
./scripts/backup-db.sh
```

Подробности и cron — в [DEPLOY.md](DEPLOY.md).
