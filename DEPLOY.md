# Деплой Nextgen Task (production)

Один HTTPS-домен обслуживает SPA и API (`/api`, `/media` проксируются nginx).

## Что нужно

1. VPS с Docker + Docker Compose
2. Домен (A-запись на IP сервера), например `tasks.example.com`
3. HTTPS снаружи: Caddy / nginx / Cloudflare Tunnel / панель хостинга

## 1. Подготовка на сервере

```bash
git clone <repo> nextgen-task
cd nextgen-task
cp .env.production.example .env.production
nano .env.production   # SECRET_KEY, пароль Postgres, домен, Bitrix (можно позже)
```

Обязательно замени:
- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- `ALLOWED_HOSTS` / `CORS_*` / `CSRF_*` / `PUBLIC_APP_URL` / `FRONTEND_URL` → твой домен
- `DEV_AUTH_BYPASS=0`

## 2. Запуск

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Проверка:

```bash
curl -I http://127.0.0.1/          # SPA
curl http://127.0.0.1/api/bitrix/install/   # JSON ok
docker compose -f docker-compose.prod.yml logs -f web worker frontend
```

## 3. HTTPS

Пример **Caddy** на хосте (порт 80 контейнера пробрось на 8080, Caddy слушает 80/443):

В `.env.production`: `HTTP_PORT=8080`

```
tasks.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Или Cloudflare Tunnel → `http://localhost:80`.

После HTTPS обнови в `.env.production` все URL на `https://…` и перезапусти:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

## 4. Bitrix24

Локальное приложение (агентство + клиенты):

| Поле | Значение |
|------|----------|
| Handler | `https://tasks.example.com/api/bitrix/install/` |
| Application URL | `https://tasks.example.com/api/bitrix/entry/` |
| Scopes | `task`, `user`, `crm` (crm — для агентства) |

`BITRIX_CLIENT_ID` / `SECRET` / `APPLICATION_TOKEN` → в `.env.production`, затем:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d web worker
```

## 5. Обновление кода

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

## Локальная проверка prod-сборки

```bash
cp .env.production.example .env.production
# поставь простые пароли, ALLOWED_HOSTS=localhost, DEBUG=0, DEV_AUTH_BYPASS=1 для смоук-теста
docker compose -f docker-compose.prod.yml --env-file .env.production up --build
```

Открой http://localhost (порт `HTTP_PORT`).
