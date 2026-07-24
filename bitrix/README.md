# Bitrix24 local application

Create a **Local application** in Bitrix24 Cloud (Developer resources).

| Field (RU) | Value |
|------------|--------|
| Путь вашего обработчика | `{PUBLIC_APP_URL}/api/bitrix/entry/` |
| Путь для первоначальной установки | `{PUBLIC_APP_URL}/api/bitrix/install/` |
| Scopes | `task`, `user`, **`crm`**, **`disk`**, **`im`** |

Install wizard must call `BX24.installFinish()` (handled by `/api/bitrix/install/`). Without it Bitrix reopen install forever.

Copy Client ID / Client Secret / Application token into `.env`.

**Агентство:** после добавления scope `crm` / `disk` / `im` переустановите/обновите права локального приложения (файлы в задачи + уведомления о комментариях + сделки).

See root `README.md` for full install flow across agency + client portals.

### Поля часов в сделке

В CRM создайте два поля типа **Число** на сделках (воронка «Сопровождение»):

1. Оплаченные часы  
2. Оставшиеся часы  

Скопируйте их коды (`UF_CRM_…`) и ID воронки в `.env`:

```
BITRIX_DEAL_PAID_HOURS_FIELD=UF_CRM_…
BITRIX_DEAL_REMAINING_HOURS_FIELD=UF_CRM_…
BITRIX_DEAL_PORTAL_LINK_FIELD=UF_CRM_1784732110930
BITRIX_COMPANY_PROJECT_ID_FIELD=UF_CRM_1784732577491
BITRIX_ACCOMPANIMENT_CATEGORY_ID=…
BITRIX_DEAL_STAGE_REPORT_REVIEW=…   # «Согласование отчёта» (или пусто — поиск по имени)
BITRIX_DEAL_STAGE_ACT_SIGNING=…     # «Подписание акта» (или пусто — поиск по имени)
```

`CATEGORY_ID` воронки смотрите в URL CRM или через `crm.category.list` / настройки воронки.
`STAGE_ID` стадий — в настройках воронки или через `crm.status.list` (`ENTITY_ID=DEAL_STAGE_{category}`).

При **отправке отчёта клиенту** сделка переходит на «Согласование отчёта»;
когда клиент **согласен** — на «Подписание акта».
Обращение к менеджеру по отчёту стадию не меняет.

При паузе/завершении задачи (закрытии сессии таймера) остаток уменьшается; оплаченные не трогаем.  
Сделка находится автоматически по полю **«Ссылка на портал Битрикс24»** (`BITRIX_DEAL_PORTAL_LINK_FIELD`).  
`GROUP_ID` Bitrix-проекта компании — из поля **«ID проекта»** на компании (`BITRIX_COMPANY_PROJECT_ID_FIELD`); робот на стадии 2 создаёт проект и пишет ID.  
В приложении «Проект» = родительская задача в этом GROUP; «Задача» = подзадача (на портале клиента — плоская задача).  
Если остаток пустой — при поиске/обновлении копируется из оплаченных.
