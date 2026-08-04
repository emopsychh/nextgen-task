#!/usr/bin/env bash
# Run on prod: bash scripts/diag_task_sync.sh [task_id]
set -euo pipefail
cd /opt/nextgen-task/nextgen-task
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.production"
TASK_ID="${1:-}"

echo "=== containers ==="
$COMPOSE ps

echo
echo "=== recent tasks ==="
$COMPOSE exec -T backend python manage.py shell <<'PY'
from board.models import Task, TimeEntry
qs = Task.objects.select_related("project", "project__portal").order_by("-id")[:15]
for t in qs:
    err = (t.sync_error or "")[:160]
    print(
        f"id={t.id} status={t.status} sync={t.sync_status} "
        f"agency_bx={t.agency_bitrix_task_id!r} "
        f"portal={t.project.portal.domain} title={t.title[:50]!r} err={err!r}"
    )
    entries = list(
        TimeEntry.objects.filter(task=t).order_by("-id").values(
            "id", "duration_seconds", "bitrix_elapsed_id", "ended_at"
        )[:5]
    )
    if entries:
        print(f"  time_entries={entries}")
PY

if [[ -n "$TASK_ID" ]]; then
  echo
  echo "=== force resync task $TASK_ID ==="
  $COMPOSE exec -T backend python manage.py shell <<PY
from board.models import Task
from board.tasks import sync_task_to_bitrix
t = Task.objects.get(pk=$TASK_ID)
print("before", t.status, t.sync_status, t.sync_error, t.agency_bitrix_task_id)
t.sync_status = Task.SyncStatus.PENDING
t.sync_error = ""
t.save(update_fields=["sync_status", "sync_error", "updated_at"])
print(sync_task_to_bitrix($TASK_ID))
t.refresh_from_db()
print("after", t.status, t.sync_status, t.sync_error, t.agency_bitrix_task_id)
PY
fi

echo
echo "=== celery / backend logs (last 80 matching lines) ==="
$COMPOSE logs --tail=200 celery backend 2>&1 | grep -Ei "sync_task|sync→bitrix|start|complete|elapsed|BitrixAPIError|ERROR|retry" | tail -80
