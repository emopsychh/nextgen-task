# Деплой Nextgen Task (production)

Один HTTPS-домен обслуживает SPA и API (`/api`, `/media` проксируются nginx).

## Модель работы

1. Агентство и клиенты входят **логином/паролем** через веб
2. Клиенты дополнительно могут открывать приложение из **своего** портала Bitrix24
3. Задачи живут в Postgres; синк в агентский Bitrix **выключен** (`BITRIX_AGENCY_TASK_SYNC=0`)
4. Пакет часов задаётся в UI агентства (не из CRM). `BITRIX_CRM_SYNC=0`

## Что нужно

1. VPS с Docker + Docker Compose
2. Домен (A-запись на IP сервера)
3. HTTPS снаружи: Caddy / nginx / Cloudflare Tunnel

## 1. Подготовка на сервере

```bash
git clone <repo> nextgen-task
cd nextgen-task
cp .env.production.example .env.production
nano .env.production
```

Обязательно:

- `SECRET_KEY`, `POSTGRES_PASSWORD`
- `ALLOWED_HOSTS` / `CORS_*` / `CSRF_*` / `PUBLIC_APP_URL` / `FRONTEND_URL`
- `DEV_AUTH_BYPASS=0`
- `BITRIX_AGENCY_TASK_SYNC=0`
- `BITRIX_CRM_SYNC=0`

## 2. Запуск

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production exec web python manage.py createsuperuser
```

В Django admin:

1. Создайте/проверьте портал агентства (`role=agency`)
2. Создайте сотрудников: Bitrix users → username + пароль, portal = agency
3. Клиентские порталы обычно появляются после установки локального приложения Bitrix; затем привяжите их в UI и задайте пакет часов на карточке клиента

Проверка:

```bash
curl -I http://127.0.0.1:${HTTP_PORT:-80}/
docker compose -f docker-compose.prod.yml logs -f web worker frontend
```

## 3. HTTPS

Пример Caddy (`HTTP_PORT=8080` в `.env.production`):

```
tasks.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

После HTTPS обновите URL в `.env.production` на `https://…` и перезапустите compose.

## 4. Bitrix24 (только клиентские порталы)

Локальное приложение на **клиентском** портале:

| Поле | Значение |
|------|----------|
| Handler | `https://tasks.example.com/api/bitrix/install/` |
| Application URL | `https://tasks.example.com/api/bitrix/entry/` |
| Scopes | минимум `user` (и что нужно клиенту для приложения) |

`BITRIX_CLIENT_ID` / `SECRET` → в `.env.production`. Агентство через Bitrix OAuth **не** входит.

## 5. Обновление кода

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

**Нельзя** делать `docker compose down -v` — это удалит volume Postgres.

## 6. Бэкап БД

```bash
chmod +x scripts/backup-db.sh
./scripts/backup-db.sh
```

Файлы: `/root/backups/nextgen-task-YYYYMMDD-HHMMSS.sql.gz` (хранение 14 дней).

Cron (ежедневно 03:15 UTC):

```
15 3 * * * cd /opt/nextgen-task/nextgen-task && ./scripts/backup-db.sh >> /var/log/nextgen-backup.log 2>&1
```

Восстановление (осторожно, перезапишет БД):

```bash
gunzip -c /root/backups/nextgen-task-….sql.gz | \
  docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U nextgen -d nextgen_task
```

## Локальная проверка prod-сборки

```bash
cp .env.production.example .env.production
# простые пароли, ALLOWED_HOSTS=localhost, DEBUG=0, DEV_AUTH_BYPASS=1 для смоука
docker compose -f docker-compose.prod.yml --env-file .env.production up --build
```
