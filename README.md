# NextGen Task — Bitrix Client Task Manager

Мультитенантный task-менеджер для агентства и клиентов внутри **Битрикс24 Cloud**.

- Агентство создаёт проекты и задачи в React-приложении на своём портале
- Клиент видит те же задачи в приложении на своём портале, пишет комментарии и прикрепляет файлы
- При создании/обновлении задача **односторонне** синхронизируется в нативные задачи Битрикс клиента

Стек: **React (Vite) + Django/DRF + Celery + PostgreSQL + Redis + Docker**

## Структура

```
backend/     Django API, Celery sync
frontend/    React SPA (agency + client modes)
bitrix/      Подсказки по локальному приложению B24
docker-compose.yml
```

## Быстрый старт (Docker)

1. Скопируйте env:

```bash
cp .env.example .env
```

2. Запустите:

```bash
docker compose up --build
```

3. Откройте:
   - Frontend: http://localhost:5173
   - API: http://localhost:8000/api/
   - Django admin: http://localhost:8000/admin/

4. Dev-вход (без Битрикс): на экране логина выберите **агентство** или **клиент** (`DEV_AUTH_BYPASS=1`).

**Продакшен (VPS):** см. [DEPLOY.md](DEPLOY.md) — `docker-compose.prod.yml` + `.env.production.example`.

Типовой сценарий локально:
1. Войти как **клиент** (создастся portal `dev-client`)
2. Выйти → войти как **агентство**
3. Привязать клиентский портал → создать проект → создать задачу
4. Снова войти как клиент → увидеть задачу, оставить комментарий / файл

Без токенов Битрикс sync получит статус `skipped` — это ожидаемо.

## Локально без Docker

### Backend

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

`CELERY_TASK_ALWAYS_EAGER=1` в корневом `.env` удобен для локального запуска без Redis. В Docker Compose для `web`/`worker` принудительно выставляется `0`.

Для фонового воркера (когда есть Redis):

```bash
celery -A config worker -l info
```

И в `.env`: `CELERY_TASK_ALWAYS_EAGER=0`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite проксирует `/api` и `/media` на `http://localhost:8000`.

## Установка в Битрикс24 Cloud

Нужен публичный **HTTPS** URL бэкенда (`PUBLIC_APP_URL`) и фронтенда (`FRONTEND_URL`).

1. Создайте **локальное приложение** на каждом портале (сначала агентство, затем 3–5 клиентов):
   - Handler: `{PUBLIC_APP_URL}/api/bitrix/install/`
   - Application URL: `{PUBLIC_APP_URL}/api/bitrix/entry/`
   - Права: `task`, `user`; для **агентства** также **`crm`** (сделки «Сопровождение» + комментарии в таймлайн)
2. Пропишите в `.env`:
   - `BITRIX_CLIENT_ID`
   - `BITRIX_CLIENT_SECRET`
   - `BITRIX_APPLICATION_TOKEN`
3. После установки откройте приложение из меню Битрикс.
4. На агентском портале укажите в `.env` `AGENCY_DOMAINS` или `AGENCY_MEMBER_IDS` — роль **Агентство** назначится сама. Остальные порталы = **Клиент**.
5. В кабинете агентства привяжите клиентские порталы — сделка сопровождения найдётся по полю «Ссылка на портал» в CRM.

Подробности: [bitrix/README.md](bitrix/README.md)

## API (основные)

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/bitrix/install/` | Install handler B24 |
| POST | `/api/bitrix/auth/` | Обмен Bitrix auth → JWT |
| POST | `/api/auth/dev/` | Dev login |
| GET | `/api/me/` | Текущий portal + user |
| CRUD | `/api/portals/`, `/api/portal-links/` | Порталы и связи |
| CRUD | `/api/deal-bindings/` | Привязка по ссылке на портал в сделке → сопровождение (agency) |
| CRUD | `/api/projects/`, `/api/tasks/` | Проекты и задачи |
| POST | `/api/tasks/{id}/timer/start/`, `…/timer/stop/` | Трекер времени (agency) |
| CRUD | `/api/comments/`, `/api/attachments/` | Комментарии и файлы |

Авторизация: `Authorization: Bearer <access>`.

## Синхронизация с Bitrix Tasks

- Источник правды — Django
- Celery-задача `board.tasks.sync_task_to_bitrix` создаёт/обновляет задачу на **портале клиента**
- Статусы: `todo→2`, `in_progress→3`, `done→5`
- Ошибки пишутся в `Task.sync_status` / `sync_error`, в UI — badge
- 2-way sync **не** входит в MVP

## Время и сделки CRM

- Агентство ведёт таймер на задаче; учёт хранится в `TimeEntry`
- При **паузе** и **завершении** закрывается сессия таймера → Celery `post_time_entry_to_deal`:
  - пишет в таймлайн сделки: `Задача «…»: учтено … . Остаток часов: N`
  - уменьшает поле **оставшихся часов** на длительность этой сессии (поле **оплаченных** не меняется)
  - повторно не списывает ту же сессию (`billed_to_deal_at`)
- Сделка ищется по полю «Ссылка на портал» (`BITRIX_DEAL_PORTAL_LINK_FIELD`, по умолчанию `UF_CRM_1784732110930`) в воронке `BITRIX_ACCOMPANIMENT_CATEGORY_ID`
- Коды полей часов в `.env`: `BITRIX_DEAL_PAID_HOURS_FIELD`, `BITRIX_DEAL_REMAINING_HOURS_FIELD` (например `UF_CRM_…`)
- Если остаток пуст, а оплачено задано — при поиске/refresh сделки остаток инициализируется из оплаченных
- Постинг идёт от токена **агентского** портала (scope `crm`)

## UI

Светлый soft-минимализм (Manrope + Sora, акцент teal). Режимы SPA:
- **Agency:** клиенты → проекты → задачи → комментарии, badge sync
- **Client:** проекты → задачи → деталка, комментарии, файлы

## Django admin

```bash
python manage.py createsuperuser
```

Удобно править роли порталов и смотреть sync вручную.