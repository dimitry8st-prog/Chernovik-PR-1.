# Sales Manager FIRST — рабочий скелет

Собственный черновой репозиторий Дмитрия Степанова. Дата сохранения: 31 октября 2026 года.

**Статус: OFFLINE_PROTOTYPE / SIMULATION / NO_OUTBOUND.**

Скелет демонстрирует настройку кампании, квалификацию компании, отдельный допуск контакта, обработку ответов, STOP, сохранение истории, follow-up, передачу менеджеру и расчёт метрик на вымышленных данных.

## Запуск

Нужен Python 3.10+ со стандартным модулем sqlite3. Устанавливать дополнительные пакеты не требуется.

```bash
git clone https://github.com/dimitry8st-prog/Chernovik-PR-1..git first-business-skeleton
cd first-business-skeleton/prototypes/first_business_rules
python -B -m unittest discover -s tests -v
python -B -m first_business demo --db demo.sqlite3 --output demo-report.json
```

В Windows можно использовать `py -3` вместо `python`. Для повторного demo укажите новый путь к БД и отчёту: существующие файлы не перезаписываются.

## Навигация

- [Подробная инструкция и возможности](prototypes/first_business_rules/README.md)
- [Код](prototypes/first_business_rules/first_business)
- [Паспорт кампании для заполнения](prototypes/first_business_rules/config/campaign.pending.json)
- [Требования и решения для Владимира](prototypes/first_business_rules/docs/REQUIREMENTS.md)
- [Этапы и переходы процесса](prototypes/first_business_rules/docs/PROCESS.md)
- [Будущие стыки интеграции](prototypes/first_business_rules/docs/INTEGRATION.md)
- [Отчёт синтетического прогона](prototypes/first_business_rules/evidence/simulation-report.json)
- [Результат локальной проверки](prototypes/first_business_rules/evidence/verification.json)

## Переиспользованные компоненты

В `SOURCE/strativon-sales-runtime-r3-2-auto/` сохранены три исходных модуля из собственных публичных репозиториев Дмитрия. Их байты сверены с зафиксированными Git blob SHA. Это зависимости локального стенда; данные, полный код и Git-история центрального приватного проекта сюда не переносились.

| Модуль | Публичный источник |
| --- | --- |
| sales_first_handoff.py | [F1-09, зафиксированная версия](https://github.com/dimitry8st-prog/Sales-Manager-FIRST-Handoff-F1-09/blob/08eecd4e07e172daf76f18cbf8a36b85dde3950d/source/sales_first_handoff.py) |
| sales_handoff_intent.py | [F1-19, зафиксированная версия](https://github.com/dimitry8st-prog/Sales-Manager-FIRST-Handoff-F1-19/blob/bce10f9e8e5dd072151be780fc06d6f16b52f719/source/sales_handoff_intent.py) |
| sales_first_send_receipt.py | [F1-19, зафиксированная версия](https://github.com/dimitry8st-prog/Sales-Manager-FIRST-Handoff-F1-19/blob/bce10f9e8e5dd072151be780fc06d6f16b52f719/source/sales_first_send_receipt.py) |

Адаптер скелета вызывает настоящие F1-09/F1-19 для подготовки пакета. Подготовка, доставка и принятие человеком учитываются отдельно. Настоящая модель, внешние провайдеры и production-адаптеры не подключены; receipts и manager ACK в demo — явно помеченные фикстуры.

Подробные сведения о центральном проекте в документации служат результатом предшествующей проверки и планом возможной интеграции. Этот репозиторий содержит самостоятельный стенд. Реальные цены, рынки, юридические основания и коммерческие целевые значения остаются параметрами на утверждение.
