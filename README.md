# Face Gate — системный дизайн проходной

Кейс: CV/ML-проход по лицу на офисном кампусе. Документация — основная часть. PoC маленький: показывает, что **allow** открывает турникет, а серая зона / spoof / stale cache — **нет**, с причиной в audit.

## Зачем бизнесу

Утром очередь съедает минуты сотрудников (в ТЗ — порядка 90 с ожидания в пике при 10 ₽/мин). Охрана тратит время на забытые карты. Карту можно передать — лицо это не лечит само по себе, но убирает типовой «приложил чужой пропуск» как единственный фактор. Авто-открытие только при уверенном матче; иначе охрана. False accept дороже ложного отказа — это заложено в пороги и kill-switch пилота. Цифры эффекта — в [docs/product.md](docs/product.md).

## Быстрый старт

Python 3.11+ (проверял на 3.14 в локальном venv — ок).

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt

pytest poc/tests/test_smoke.py -q
python scripts/demo.py
```

CI то же самое гоняет на GitHub Actions (`.github/workflows/ci.yml`) при push/PR в `main`.

API (опционально):

```bash
uvicorn poc.app.main:app --reload --port 8000
# POST http://localhost:8000/v1/access/verify
```

Или: `docker compose up` (проброс `:8000`).

## Что демонстрирует PoC

Пять событий из ТЗ:

| event | ожидание |
|-------|----------|
| e-1001 | `allow` + `open` |
| e-1002 | quality → `manual_review`, не open |
| e-1003 | spoof → `deny`, не open |
| e-1004 | малый margin → `manual_review`, не open |
| e-1005 | offline + stale cache → `manual_review` + `degraded_mode` |

Контракт: `POST /v1/access/verify` как в задании. Audit: `poc/data/audit.jsonl` (в `.gitignore`, появляется после прогона).

## Real vs mock

| Часть | В PoC | В целевой архитектуре |
|-------|-------|------------------------|
| Policy / 3 исхода / fail-closed | **реально**, `decision.py` | то же на edge |
| Cosine top-2 | есть код | + HNSW на больших базах |
| Quality / liveness / match scores | **fixture** в `events.json` | SCRFD, PAD, ArcFace |
| Турникет | поле в JSON | SDK + ack |
| Охрана | reasons в логе | очередь оператора |
| Кадры | `file://demo/...`, без реальных лиц | RTSP |

Подробности: [docs/architecture.md](docs/architecture.md), [docs/ml.md](docs/ml.md).

## Документация

- [docs/architecture.md](docs/architecture.md) — edge-first hybrid, потоки, хранилища
- [docs/ml.md](docs/ml.md) — пайплайн, пороги, validation, почему не LLM
- [docs/monitoring.md](docs/monitoring.md) — метрики и алерты
- [docs/product.md](docs/product.md) — ценность, гипотезы, эффект, пилот
- [docs/risks-and-ops.md](docs/risks-and-ops.md) — latency/offline и privacy
- [AI_USAGE.md](AI_USAGE.md) · [WORKLOG.md](WORKLOG.md) · [SELF_REVIEW.md](SELF_REVIEW.md)

## Допущения и дыры MVP

- Цена FA = 250 000 ₽/инцидент (assumption).
- PoC не меряет FAR/FRR и не гоняет реальные модели.
- Нет железа турникета, нет UI охраны, нет юридического контура согласия в коде.
- Invite проверяющему (`aitalenthub-study` и т.п.) — на стороне сдающего.

Перед продом: согласие и процессы, реальный PAD/FR на камерах кампуса, калибровка порогов по identity-split, kill-switch и мониторинг из docs.
