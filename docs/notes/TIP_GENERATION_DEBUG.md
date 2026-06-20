# Кейс: галлюцинация ADHD-tip на главном экране Mini App

> Учебный разбор. Issue #71. Файл: `miniapp/backend/routes/today.py`,
> функция `_generate_adhd_tip`. Тесты: `tests/test_tip_validation.py`.

## 1. Симптом

На главном экране Today (вкладка «Мой день» Nexus) совет дня вместо
конкретной фразы показал плейсхолдер — буквально слово «штука» в тексте
вроде «положи штуку в одно место». Совет бесполезный: непонятно про что,
звучит как заглушка, а не как помощь внешнего мозга.

## 2. Диагностика — 7 слоёв

1. **`temperature` не задан** → Anthropic API берёт дефолт (1.0).
   Максимальная вариативность там, где нужна предсказуемость короткой
   фразы.
2. **`max_tokens=200` при инструкции «15 слов»** → нет жёсткого
   потолка длины на уровне API; модель свободна растекаться.
3. **System prompt без negative constraints** — не запрещены
   «штука / вещь / что-то / нечто». Модели не сказали, чего НЕ делать.
4. **Нет few-shot examples** — модель не видела эталон «хорошо vs плохо»,
   опиралась только на абстрактное описание.
5. **Output validation отсутствует** — что бы Haiku ни вернул, оно шло
   на экран как есть.
6. **Кеш на 24ч даже для брака** — плохой совет залипал в кеше
   (`cache.set_tip`) на сутки; refresh вручную (`/today/refresh-tip`)
   не делается автоматически.
7. **Context для промпта тонкий** — память берётся «3 произвольные
   записи по категории 🦋 СДВГ» (`_adhd_context_memories`, `page_size=3`),
   без семантической релевантности к активным задачам.

## 3. Корневая причина

Связка из двух дыр: **галлюцинация** (пункты 1–4 повышают её
вероятность) + **отсутствие защитного слоя** (пункты 5–6 пропускают брак
до пользователя и фиксируют его в кеше). Любой генеративный вывод без
валидации — это «надеемся, что модель не ошибётся».

## 4. Что починили сейчас

- **Tuning параметров API** (`_ask_tip`): `temperature=0.4` (стабильнее,
  но не зажато в ноль) + `max_tokens=80` (жёсткий потолок под «15 слов»).
  Потребовало добавить проброс `temperature` в `core/claude_client.py:ask_claude`.
- **Negative prompting в system** (`_TIP_SYSTEM`): явный список запретов —
  плейсхолдеры, общие фразы, markdown, эмодзи, двоеточие в начале.
- **Few-shot examples**: два ХОРОШО (конкретные, с триггером-причиной) и
  два ПЛОХО (плейсхолдеры / общая фраза) прямо в system prompt.
- **Output validation** (`_validate_tip`): отсев по словарю
  `PLACEHOLDER_WORDS` (формы «штука/вещь/что-то/нечто/что-нибудь») +
  границы длины (5–20 слов). Возвращает `(valid, reason)` для логов.
- **Retry**: Haiku недетерминирован — первый брак → один повтор тем же
  промптом. Второй брак → статичный `_FALLBACK_TIP`, который **не
  кешируется** (следующий заход попробует снова).
- **Не отдаём плохой кеш**: чтение кеша теперь прогоняется через
  `_validate_tip` — залипший брак больше не показывается.
- **Логирование**: `tip_quality_fail` с текстом-браком и причиной
  (`logger.warning`) на каждом retry/fallback — видно в stderr.

## 5. Future work (отложено)

Сделано осознанно «потом», чтобы не раздувать фикс. Триггер для возврата —
миграция хранилища с Notion-памяти на Postgres.

- **Reuse `_classify_adhd()` для structured context.** Сейчас контекст —
  3 случайные записи. После миграции типы памяти (`user / feedback /
  project / reference` или ADHD-категории) станут полями БД → можно
  собирать релевантный по типу контекст вместо случайного среза.
- **RAG через Qdrant.** Векторный поиск по памяти: эмбеддинг активных
  задач → top-k семантически близких записей о её СДВГ → совет
  привязан к тому, что реально происходит сегодня, а не к случайной
  выборке.
- **Eval framework v1.** Golden dataset из 30+ кейсов (вход: задачи +
  память; ожидание: проходит/не проходит валидацию + ручная оценка
  качества). Batch-прогон на каждое изменение промпта → регрессии
  ловятся до прода, как сейчас `test_models_audit.py` ловит дрейф на
  Sonnet.

## 6. Lessons learned (инженерные выводы)

- **`temperature` и `max_tokens` на дефолте — антипаттерн.** Дефолт
  (temp=1.0, большой лимит) почти никогда не то, что нужно конкретной
  задаче. Задавать осознанно под формат вывода.
- **Negative prompting + few-shot снижают галлюцинации.** Сказать, чего
  НЕ делать, и показать эталон — дешевле и надёжнее, чем потом ловить
  брак.
- **Output validation — обязательный defensive-слой любой AI-системы.**
  Генеративный вывод нельзя доверять «как есть»; нужен детерминированный
  чек между моделью и пользователем (+ retry, + fallback).
- **Архитектурный reuse прежде нового кода.** Перед тем как писать сбор
  контекста заново — проверить, что уже есть (`_classify_adhd`,
  `core/memory.py`). Сёстры Nexus/Arcana — про это же.
- **ROI-оценка фикса: переносимое vs специфичное.** Validation + retry +
  negative prompting переносимы в любой вызов Claude в репо. RAG/eval —
  специфичны для текущей архитектуры памяти и ждут её миграции.

## 7. Связь с теорией

- Anthropic — Prompt engineering overview:
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview
- Anthropic — Use examples (multishot / few-shot):
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting
- Anthropic — Be clear and direct (negative constraints):
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct
- Anthropic — Increase output consistency (temperature, structure):
  https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/increase-consistency
- RAG (Retrieval-Augmented Generation) — концепция для отложенной части:
  https://docs.anthropic.com/en/docs/build-with-claude/embeddings
