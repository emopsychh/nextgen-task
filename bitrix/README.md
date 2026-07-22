# Bitrix24 local application

Create a **Local application** in Bitrix24 Cloud (Developer resources).

| Field | Value |
|-------|--------|
| Handler | `{PUBLIC_APP_URL}/api/bitrix/install/` |
| Application URL | `{PUBLIC_APP_URL}/api/bitrix/entry/` |
| Scopes | `task`, `user`, **`crm`** (нужен агентству: сделки + timeline-комментарии) |

Copy Client ID / Client Secret / Application token into `.env`.

**Агентство:** после добавления scope `crm` переустановите/обновите права локального приложения, иначе пост в сделку вернёт ошибку доступа.

See root `README.md` for full install flow across agency + client portals.

### Поля часов в сделке

В CRM создайте два поля типа **Число** на сделках (воронка «Сопровождение»):

1. Оплаченные часы  
2. Оставшиеся часы  

Скопируйте их коды (`UF_CRM_…`) в `.env`:

```
BITRIX_DEAL_PAID_HOURS_FIELD=UF_CRM_…
BITRIX_DEAL_REMAINING_HOURS_FIELD=UF_CRM_…
```

При завершении задачи в таск-менеджере остаток уменьшается; оплаченные не трогаем.  
Если остаток пустой — при привязке сделки копируется из оплаченных.
