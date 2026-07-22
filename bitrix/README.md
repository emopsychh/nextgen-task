# Bitrix24 local application

Create a **Local application** in Bitrix24 Cloud (Developer resources).

| Field (RU) | Value |
|------------|--------|
| Путь вашего обработчика | `{PUBLIC_APP_URL}/api/bitrix/entry/` |
| Путь для первоначальной установки | `{PUBLIC_APP_URL}/api/bitrix/install/` |
| Scopes | `task`, `user`, **`crm`** (агентство) |

Install wizard must call `BX24.installFinish()` (handled by `/api/bitrix/install/`). Without it Bitrix reopen install forever.

Copy Client ID / Client Secret / Application token into `.env`.

**Агентство:** после добавления scope `crm` переустановите/обновите права локального приложения, иначе пост в сделку вернёт ошибку доступа.

See root `README.md` for full install flow across agency + client portals.

### Поля часов в сделке

В CRM создайте два поля типа **Число** на сделках (воронка «Сопровождение»):

1. Оплаченные часы  
2. Оставшиеся часы  

Скопируйте их коды (`UF_CRM_…`) и ID воронки в `.env`:

```
BITRIX_DEAL_PAID_HOURS_FIELD=UF_CRM_…
BITRIX_DEAL_REMAINING_HOURS_FIELD=UF_CRM_…
BITRIX_ACCOMPANIMENT_CATEGORY_ID=…
```

`CATEGORY_ID` воронки смотрите в URL CRM или через `crm.category.list` / настройки воронки.

При паузе/завершении задачи (закрытии сессии таймера) остаток уменьшается; оплаченные не трогаем.  
Сделка находится автоматически по полю **«Ссылка на портал Битрикс24»** (`UF_CRM_1784732110930` / `BITRIX_DEAL_PORTAL_LINK_FIELD`): в сделке должна быть ссылка на портал клиента.  
Если остаток пустой — при поиске/обновлении копируется из оплаченных.
