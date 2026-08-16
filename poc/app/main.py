"""
main.py — HTTP API (FastAPI).

Эндпоинты:
  GET  /health              — жив ли сервис + версии
  GET  /metrics             — простые счётчики (вместо Prometheus на демо)
  POST /v1/access/verify    — главный контракт ТЗ
  POST /v1/admin/revoke     — снять шаблон с edge-кеша
  GET  /v1/guard/queue      — очередь manual_review (JSON)
  GET  /ui/guard            — простой экран охраны с reasons (сценарий 7)
  POST /v1/guard/review/{id}— решение охраны open/deny
"""

from __future__ import annotations

from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from poc.app.metrics import metrics
from poc.app.models import (
    AccessVerifyRequest,
    AccessVerifyResponse,
    GuardResolveRequest,
    GuardResolveResponse,
    RevokeRequest,
    RevokeResponse,
)
from poc.app.pipeline import MODEL_VERSION, append_operator_audit, process_event
from poc.app.store import gallery_store, guard_queue
from poc.app.turnstile import turnstile

app = FastAPI(
    title="Face Gate PoC",
    version="0.2.0",
    description="Policy PoC + prod-shaped edge pieces (turnstile ack, revoke, guard queue, metrics).",
)


@app.get("/health")
def health() -> dict[str, object]:
    """Проверка живости + какая «модель»/policy сейчас на узле."""
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "policy_version": gallery_store.policy_version,
    }


@app.get("/metrics")
def get_metrics() -> dict[str, object]:
    """Счётчики для демо-наблюдаемости (в проде — Prometheus)."""
    snap = metrics.snapshot()
    snap["policy_version"] = gallery_store.policy_version
    snap["guard_queue_open"] = len(guard_queue.list_open())
    return snap


@app.post("/v1/access/verify", response_model=AccessVerifyResponse)
def verify_access(body: AccessVerifyRequest) -> AccessVerifyResponse:
    """Кадр/событие → решение → команда турникету."""
    return process_event(body)


@app.post("/v1/admin/revoke", response_model=RevokeResponse)
def revoke_access(body: RevokeRequest) -> RevokeResponse:
    """
    Модель приоритетного отзыва с центра на edge:
    шаблон пропадает из локального кеша, policy_version растёт.
    """
    revoked = gallery_store.revoke(body.employee_id)
    if revoked:
        metrics.revocations += 1
    return RevokeResponse(
        employee_id=body.employee_id,
        revoked=revoked,
        policy_version=gallery_store.policy_version,
    )


@app.get("/v1/guard/queue")
def guard_queue_list() -> dict[str, object]:
    """Что сейчас ждёт ручной разбор (JSON для API/тестов)."""
    return {"items": guard_queue.list_open()}


@app.get("/ui/guard", response_class=HTMLResponse)
def guard_ui() -> str:
    """
    Экран охраны для сценария №7.

    Важно: очередь живёт в памяти процесса uvicorn.
    `python scripts/demo.py` эту страницу не наполняет — события нужно
    прогнать через этот же сервер (кнопка ниже или POST /v1/access/verify).
    """
    items = guard_queue.list_open()
    rows: list[str] = []
    for item in items:
        reasons = ", ".join(escape(str(r)) for r in item.get("reasons", []))
        eid = escape(str(item.get("event_id", "")))
        rows.append(
            "<tr>"
            f"<td>{eid}</td>"
            f"<td>{escape(str(item.get('gate_id', '')))}</td>"
            f"<td>{escape(str(item.get('employee_id') or '—'))}</td>"
            f"<td><code>{reasons}</code></td>"
            "<td>hold</td>"
            f"<td>"
            f"<button type='button' onclick=\"resolveEvent('{eid}','open')\">открыть</button> "
            f"<button type='button' onclick=\"resolveEvent('{eid}','deny')\">отказать</button>"
            f"</td>"
            "</tr>"
        )
    body_rows = (
        "\n".join(rows)
        if rows
        else (
            '<tr><td colspan="6"><strong>Очередь пуста.</strong> '
            "Нажми «Загрузить рисковые события» ниже — "
            "или сначала сделай POST /v1/access/verify на этот же сервер. "
            "Скрипт demo.py сюда не пишет.</td></tr>"
        )
    )
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <title>Охрана — очередь review</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 1100px; }}
    h1 {{ font-size: 1.25rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #ccc; padding: 0.5rem 0.75rem; text-align: left; vertical-align: top; }}
    th {{ background: #f4f4f4; }}
    code {{ font-size: 0.85rem; }}
    .hint {{ color: #333; margin: 0.75rem 0; line-height: 1.45; }}
    .actions {{ display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 1rem 0; }}
    button {{ cursor: pointer; padding: 0.4rem 0.75rem; }}
    #status {{ min-height: 1.25rem; color: #064; }}
  </style>
</head>
<body>
  <h1>Очередь ручной проверки</h1>
  <p class="hint">
    Рискованный проход сам турникет <strong>не</strong> открывает.
    Здесь видны события с причиной (<code>reasons</code>).
    Очередь хранится в памяти сервера — после перезапуска uvicorn она пустая.
  </p>
  <div class="actions">
    <button type="button" id="btn-seed" onclick="seedRisky()">Загрузить рисковые события</button>
    <button type="button" onclick="location.reload()">Обновить страницу</button>
  </div>
  <p id="status"></p>
  <table>
    <thead>
      <tr>
        <th>event_id</th>
        <th>gate</th>
        <th>employee</th>
        <th>reasons</th>
        <th>турникет</th>
        <th>действие охраны</th>
      </tr>
    </thead>
    <tbody>
      {body_rows}
    </tbody>
  </table>
  <p class="hint">
    API: <a href="/docs">/docs</a> · JSON очереди: <a href="/v1/guard/queue">/v1/guard/queue</a>
  </p>
  <script>
    const RISKY = [
      {{
        event_id: "e-1002", gate_id: "gate-1", camera_id: "cam-1b",
        captured_at: "2026-07-31T08:57:41Z",
        frame_uri: "file://demo/frames/e-1002.jpg",
        metadata: {{ direction: "in", illumination: "backlight", occlusion_hint: "mask",
          edge_node: "edge-gate-1", network: "online" }}
      }},
      {{
        event_id: "e-1004", gate_id: "gate-2", camera_id: "cam-2b",
        captured_at: "2026-07-31T09:05:22Z",
        frame_uri: "file://demo/frames/e-1004.jpg",
        metadata: {{ direction: "in", illumination: "dim", head_pose_hint: "yaw_30",
          edge_node: "edge-gate-2", network: "online" }}
      }},
      {{
        event_id: "e-1005", gate_id: "gate-1", camera_id: "cam-1a",
        captured_at: "2026-07-31T09:11:58Z",
        frame_uri: "file://demo/frames/e-1005.jpg",
        metadata: {{ direction: "in", edge_node: "edge-gate-1", network: "offline",
          cache_age_minutes: 240 }}
      }},
      {{
        event_id: "e-1006", gate_id: "gate-1", camera_id: "cam-1a",
        captured_at: "2026-07-31T09:14:10Z",
        frame_uri: "file://demo/frames/e-1006.jpg",
        metadata: {{ direction: "in", edge_node: "edge-gate-1", network: "online" }}
      }},
      {{
        event_id: "e-1007", gate_id: "gate-2", camera_id: "cam-2a",
        captured_at: "2026-07-31T09:16:40Z",
        frame_uri: "file://demo/frames/e-1007.jpg",
        metadata: {{ direction: "in", edge_node: "edge-gate-2", network: "online" }}
      }}
    ];

    async function seedRisky() {{
      const status = document.getElementById("status");
      const btn = document.getElementById("btn-seed");
      btn.disabled = true;
      status.textContent = "Отправляю события на /v1/access/verify…";
      try {{
        for (const body of RISKY) {{
          const res = await fetch("/v1/access/verify", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify(body)
          }});
          if (!res.ok) throw new Error("verify failed for " + body.event_id);
        }}
        status.textContent = "Готово. Обновляю…";
        location.reload();
      }} catch (err) {{
        status.textContent = "Ошибка: " + err;
        btn.disabled = false;
      }}
    }}

    async function resolveEvent(eventId, action) {{
      const status = document.getElementById("status");
      status.textContent = eventId + " → " + action + "…";
      try {{
        const res = await fetch("/v1/guard/review/" + encodeURIComponent(eventId), {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ action: action, operator_id: "guard-ui" }})
        }});
        if (!res.ok) {{
          const t = await res.text();
          throw new Error(t || res.status);
        }}
        location.reload();
      }} catch (err) {{
        status.textContent = "Ошибка: " + err;
      }}
    }}
  </script>
</body>
</html>"""


@app.post("/v1/guard/review/{event_id}", response_model=GuardResolveResponse)
def guard_resolve(event_id: str, body: GuardResolveRequest) -> GuardResolveResponse:
    """Охранник подтвердил open или окончательный deny — пишем audit + турникет."""
    item = guard_queue.resolve(
        event_id, action=body.action, operator_id=body.operator_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="no open review for event_id")

    metrics.guard_actions += 1
    append_operator_audit(
        event_id=event_id,
        action=body.action,
        operator_id=body.operator_id,
        audit_id=item.get("audit_id", "a-unknown"),
    )

    if body.action == "open":
        ack = turnstile.apply(event_id=event_id, command="open")
    else:
        ack = turnstile.apply(event_id=event_id, command="hold")

    return GuardResolveResponse(
        event_id=event_id,
        status="resolved",
        operator_action=body.action,
        turnstile_status=ack.status,
    )
