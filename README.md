# SalesParser

Ежедневно собирает каталоги и цены с заданных сайтов, сопоставляет одинаковые товары по EAN/артикулу
и показывает единый каталог, где по каждой позиции подсвечена минимальная цена среди источников.

## Как это устроено

```
sources/*.yaml ──► worker (APScheduler, cron) ──► адаптер сайта ──► Postgres ──► api (FastAPI + Jinja)
                       ▲                         generic / playwright /      │
                       └── очередь ручных запусков из /admin                 └─► /, /product/{id}, /admin
```

1. **Обход источника**: стартовая страница → ссылки категорий → подкатегории (дерево строится по тому,
   на какой странице найдена ссылка) → листинги листовых категорий с пагинацией → при необходимости
   карточка товара (CSS-селекторы и/или schema.org JSON-LD).
2. **Снапшот цены** — одна запись на товар источника в день; повторный обход в тот же день её перезаписывает.
3. **Единое дерево категорий** берётся с ведущего источника (`is_master: true`). Категории остальных
   источников с тем же названием привязываются автоматически, остальные — вручную в `/admin/categories`.
4. **Матчинг**: сначала по EAN (с проверкой контрольной цифры, UPC→EAN-13), затем по нормализованному
   артикулу (`SM-A556B` = `sm a556b` = `SМ-А556В` с кириллицей). Товары без обоих ключей попадают
   в `/admin/unmatched`, там их привязывают к позиции (с подсказками похожих по названию) или создают новую.
5. **Анализ**: для каждой позиции берётся последняя цена каждого источника (не старше `STALE_DAYS`),
   считаются минимум и максимум среди предложений в наличии, разброс в %, изменение к предыдущему
   обходу, а по источникам — в скольких позициях он дешевле всех.

В каталоге минимальная цена — зелёная (при равенстве подсвечиваются все), максимальная — красная,
«нет в наличии» — зачёркнута и в сравнении не участвует. Выгрузка текущей выборки — кнопка CSV.

## Подключение реального сайта

Создайте `sources/<name>.yaml` (примеры — `sources/demo-*.yaml`):

```yaml
name: shop1                 # уникальный ключ
title: Магазин 1            # название колонки в каталоге
base_url: https://shop1.ru/
is_master: false            # true — ровно у одного источника, по нему строится дерево
adapter: generic            # generic | playwright | "package.module:ClassName"
config:
  delay: 1.5                # пауза между запросами, с
  respect_robots: true
  categories:
    start_url: /catalog/
    link: "nav.catalog > ul > li > a"   # корневые категории на стартовой странице
    sub_link: ".subcategories a"        # подкатегории на странице категории
    exclude: "/sale/|/brands/"          # регулярка по URL, что пропустить
  listing:
    item: ".product-card"
    fields:
      url: "a.title::attr(href)"
      name: "a.title"                   # без ::text берётся весь текст элемента
      price: ".price-current"
      old_price: ".price-old"
      sku: {sel: ".article", re: "Арт\\.?\\s*(\\S+)"}
      ean: "xpath:@data-barcode"
      brand: ".brand"
      in_stock: ".availability"
    next_page: "a.pagination-next::attr(href)"
  in_stock_pattern: "в наличии|есть"
  out_of_stock_pattern: "нет в наличии|под заказ"
  detail:                     # карточка товара, если в листинге нет EAN/артикула
    enabled: true
    jsonld: true              # schema.org Product: sku, gtin13, brand, offers.price
    when_missing: [ean, sku]
    fields:
      ean: ".specs .barcode"
```

* Сайт рендерит каталог на JS — `adapter: playwright` (и `wait_for: ".product-card"` в `config`),
  а worker соберите с `WITH_PLAYWRIGHT=1`.
* Совсем нестандартный сайт (API, бесконечная прокрутка) — класс-наследник `app.scraping.base.SourceAdapter`
  с методами `categories()` и `products(category)`, подключается как `adapter: "app.adapters.shop1:Shop1Adapter"`.
* YAML подхватывается при старте api/worker. Флаг «включён» после первого импорта меняется в админке;
  источник, чей файл удалён, автоматически выключается.

Проверить конфиг без ожидания расписания: `docker compose exec worker python -m app.worker run --source shop1`,
затем смотреть `/admin/runs`.

## Доступ

Весь сайт, кроме страницы входа и демо-магазинов, доступен только после входа (как в icebreaker):
пароли хранятся в виде scrypt-хеша, сессия — подписанная `SECRET_KEY` HttpOnly-cookie на 30 дней,
пользователь перечитывается из базы на каждом запросе, POST с чужих страниц отсекаются по `Origin`.

| Роль | Что может |
|---|---|
| Наблюдатель | каталог, карточки, CSV |
| Менеджер | + админка: запуск сбора, маппинг категорий, сопоставление товаров |
| Администратор | + пользователи (`/admin/users`) |

Первый администратор создаётся из `ADMIN_LOGIN`/`ADMIN_PASSWORD`, когда таблица пользователей пуста.
Эти же данные — аварийный вход: работают, даже если пароль в базе забыт или пользователь отключён.

## Деплой в Dokploy

1. Создайте Compose-проект из этого репозитория, в Environment задайте переменные из `.env.example`
   (обязательно `DOMAIN` — без `https://` и слеша, `SECRET_KEY`, `ADMIN_PASSWORD`, `POSTGRES_PASSWORD`).
2. Маршрут описан Traefik-лейблами в `docker-compose.yml`, `api` подключён к внешней сети `dokploy-network`
   — домен в UI Dokploy добавлять не нужно.
3. Healthcheck `api` выполняется через Python (в slim-образе нет wget/curl). Если Traefik отдаёт 404
   или сертификат по умолчанию, сначала проверьте, что контейнер `api` в статусе `healthy`.
4. Миграции применяются при старте `api`; worker стартует после того, как api стал healthy.
5. Когда реальные источники подключены — `DEMO_ENABLED=false`.

## Локальная разработка

```bash
uv venv -p 3.12 && uv pip install -e ".[dev]"
docker run -d --name sp-db -e POSTGRES_USER=salesparser -e POSTGRES_PASSWORD=salesparser \
  -e POSTGRES_DB=salesparser -p 127.0.0.1:5432:5432 postgres:16-alpine
alembic upgrade head
ADMIN_PASSWORD=secret123 uvicorn app.main:app --port 8000   # вход admin / secret123
python -m app.worker run                  # разовый обход
```

Тесты (интеграционные очищают указанную БД):

```bash
TEST_DATABASE_URL=postgresql+asyncpg://salesparser:salesparser@127.0.0.1:5432/salesparser_test pytest
```

## Структура

| Путь | Что внутри |
|---|---|
| `app/scraping/` | HTTP-клиент (паузы, ретраи, robots.txt), generic- и playwright-адаптеры, реестр |
| `app/runner.py` | обход одного источника, запись категорий/товаров/снапшотов, журнал запусков |
| `app/matching.py`, `app/catalog.py` | матчинг по EAN/артикулу, единое дерево и автомаппинг категорий |
| `app/analysis.py` | SQL-аналитика: актуальные предложения, мин/макс, разброс, динамика, история |
| `app/worker.py` | планировщик и очередь ручных запусков, CLI |
| `app/web/` | страницы каталога, карточки и админки |
| `app/demo/` | три демо-магазина с разной вёрсткой для проверки конвейера |
