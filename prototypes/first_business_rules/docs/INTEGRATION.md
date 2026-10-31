# FIRST: граница прототипа и стыки интеграции

Дата сверки: 31 октября 2026 года. Статус: **OFFLINE_PROTOTYPE / NO_OUTBOUND**.

Место хранения стенда: собственный репозиторий `dimitry8st-prog/Chernovik-PR-1.`. Ссылки на центральные `project_records`, controller и прочие модули ниже описывают ранее проверенный проект и будущие стыки интеграции. Они не являются локальными зависимостями этого самостоятельного стенда. Для запуска включены три публичных исходных модуля F1-09/F1-19; происхождение указано в корневом README.

Этот каталог демонстрирует исполнимую бизнес-логику на синтетических данных. Он не подключает рабочую очередь, Make, почту, CRM, реальных клиентов или платёжные инструменты. Успешный тест прототипа не является приёмкой CP12, полной системы или установленного runtime.

## Проверенная исходная версия

- Репозиторий: `angle-theory-studio/sales-manager-first` (приватный).
- Проверенный `main`: `c4964b4be87d3148a362f9a4b527ac475fca04b9`, коммит от 24 октября 2026 года; при сверке 31 октября SHA не изменился.
- Основная база: `SOURCE/strativon-sales-runtime-r3-2-auto/`.
- Обязательные проектные записи: `project_records/Sales_First_Integration_Controller_State.json` и `project_records/Sales_Manager_FIRST_Developer_Register.md`.
- `TRANSFER_STATUS.md` подтверждает статический copy-only перенос. Его PASS не подтверждает готовность приложения, установку, интеграцию или разрешение отправок.
- В полном дереве проверенной версии `AGENTS.md` не найден. Ограничения текущего задания и существующие назначения при этом сохраняются.

Изолированный прототип не изменяет принятые компоненты, общие модули, исходные DTO или текущие контрольные записи. Новое подтверждение приёма менеджером и демонстрационная классификация намерений не выдаются за уже принятые функции центрального проекта.

## Точные существующие швы

Все пути таблицы относительны `SOURCE/strativon-sales-runtime-r3-2-auto/` в указанном коммите.

| Путь | Существующий интерфейс | Что важно сохранить |
| --- | --- | --- |
| `sales_first_binding.py` | `validate_first_binding(config, *, now)` | `FIRST_BINDING_INPUT_V1` -> `FIRST_BINDING_V1`; доверенная конфигурация сервера, проверка сроков и `release_mode=NO_OUTBOUND`. |
| `sales_runtime_binding.py` | `resolve_campaign_projection(campaign, *, resolve_config, now)` | Resolver передаётся сервером; `campaign["config"]` не расширяет полномочия. В исходнике статус `NOT_CONNECTED`. |
| `sales_discovery_qualification.py` | `qualify_candidate(candidate, assessment, *, binding, policy, now)` | `FIRST_QUALIFICATION_RESULT_V1`: `QUALIFIED_FOR_PREPARATION`, `NOT_QUALIFIED` или `NEEDS_REVIEW`. Квалификация не означает разрешение обращения. |
| `sales_runtime_qualification.py` | `qualify_and_ingest(candidate, *, binding, policy, resolve_assessment, ingest, now)` | Доверенные callback-порты и копии данных. Вызывает ingest только после допуска к подготовке. |
| `sales_first_decision.py` | `decide_next_action(state, event, policy, *, now)` | `FIRST_DIALOGUE_STATE_V1`, `FIRST_DIALOGUE_EVENT_V1`, `FIRST_POLICY_V1` -> `FIRST_DECISION_V1`; `PROPOSE_REPLY` не является отправкой. |
| `sales_first_handoff.py` | `assemble_handoff_context(context)` | Точный `FIRST_HANDOFF_INPUT_V1` -> `FIRST_HANDOFF_CONTEXT_V1`; scope, история, evidence, SHA256 и `delivery_status=NOT_DISPATCHED`. |
| `sales_handoff_intent.py` | `build_handoff_intent(context, recipient, interest)` | `FIRST_HANDOFF_RECIPIENT_V1` + `FIRST_INTEREST_EVIDENCE_V1`; `QUESTION`/`UNCERTAIN` дают HOLD, только `INTEREST` даёт PREPARED. |
| `sales_first_send_receipt.py` | `normalize_send_receipt(attempt, receipt)` | `FIRST_SEND_ATTEMPT_V1` + `FIRST_PROVIDER_SEND_RESULT_V1` -> `FIRST_SEND_RECEIPT_V1`. ACCEPTED и DELIVERED различны; TIMEOUT -> UNKNOWN; автоматического разрешения повтора нет. |
| `first_handoff_integration/notification_receipt.py` | `normalize_notification_receipt(intent, *, action_id, recipient_ref, attempt, receipt)` | Квитанция связывается с конкретным пакетом, получателем, scope, хешем и попыткой. Это не подтверждение принятия менеджером. |

F1-19 проверяет переданное решение об интересе; он не является классификатором свободного текста. Контракт оценки намерения, её доказательства и человеческая проверка требуют самостоятельного согласования и тестирования. Языковой P19 уже имеет отдельное назначение и не заменяется этим прототипом.

## Что говорит текущий controller

`current_run.latest_checkpoint` равен `SFA-RESUMED-20261023-CP12_CANONICAL-ACCEPTED`. Принят изолированный canonical helper. Следующая работа - TEST_ONLY-композиция с настоящим dedupe index/lookup и 12 новыми семействами проверок. Она не выполнена данным прототипом. Полная runtime-дедупликация, весь P02, 17 требований, 8 E2E и 44 полные группы проверок не объявляются пройденными.

Записаны контрольные EN/RU/UK и семь последовательных календарных суток наблюдения после технической приёмки на будущей рабочей площадке. Эти семь суток не доказывают достаточность выборки для коммерческой конверсии или стоимости лида; коммерческий эксперимент требует собственного обоснования.

`config/sales_manager_target_policy.json` имеет `runtime_applied:false` и не является переключателем разрешения отправок. `config/first_direction.json` содержит профиль STRATIVON как исходную конфигурацию, но не подтверждает актуальные цены, юридический допуск или выбранные рынки будущего пилота.

## Порядок последующего подключения

1. Утвердить паспорт одной кампании: продукт, доказанные факты, условия, целевую группу, рынок, канал, правовую проверку, менеджера и бюджет. Оформить недостающие параметры как блокирующие решения, а не подставлять правдоподобные значения.
2. Зафиксировать текущие зависимости, назначение работы, допустимые файлы и независимую проверку центрального проекта. Отдельно проверить семантическое соответствие бизнес-правил действующим DTO.
3. Сначала подключить read-only/shadow проекцию: доверенный конфиг -> события -> решения -> локальный журнал. Синтетические receipt и ACK остаются помеченными `simulation:true`.
4. Для будущих внешних адаптеров сохранить независимую проверку непосредственно перед действием: scope, актуальную версию правил, STOP, лимиты, разрешённого адресата и необходимые основания. Текст клиента или ответ модели не меняет эти правила.
5. Подключать provider receipt и человеческий ACK раздельно. Тайм-аут после внешнего эффекта - UNKNOWN до сверки, а не повод отправлять повторно.
6. Проводить установленную техническую приёмку и отдельно согласованный ограниченный пилот. Фактическое подключение, deployment и живые отправки не являются результатом текущего каталога.

## Контракт метрик прототипа

`first_business.metrics.calculate_metrics(events, costs, *, start, end)` - чистая функция без I/O. Она возвращает JSON-совместимый отчёт `scope=SIMULATION`, `real_business_evidence=false`.

- Cohort identity: `(campaign_id, lead_id)`. Используется самое раннее `LEAD_INGESTED` в предоставленной истории; оно должно лежать в `[start, end)`. Повтор старого ingestion внутри периода не превращает старый лид в новый.
- Последующие наблюдения и расходы входят при `ingested_at <= occurred_at <= end`. Входные границы и даты требуют timezone.
- Каждая rate содержит `numerator`, `denominator`, `value`; при нулевом знаменателе `value=null`.
- Квалификация: QUALIFIED / cohort. Допуск контакта: CONTACT_ALLOWED среди QUALIFIED / QUALIFIED.
- Доставка: число лидов хотя бы с одним подтверждённым SEND_RECEIPT=DELIVERED / число лидов с SEND_RECEIPT. `ever_delivered_leads` сохраняет факт подтверждённой доставки даже при последующем UNKNOWN по follow-up. Последние состояния для сверки вынесены отдельно в счётчики `send_accepted`, `send_delivered`, `send_unknown`; это не числитель конверсии доставки. Переход ACCEPTED -> DELIVERED считается одним лидом; отдельно выводятся количества сырых наблюдений.
- Ответы и интерес: соответствующие лиды среди когда-либо подтверждённо доставленных / когда-либо подтверждённо доставленные. В human reply входят `QUESTION`, `REQUEST_INFO`, `OBJECTION`, `REFUSAL`, `STOP`, `INTEREST`. `AUTO_REPLY`, `UNCERTAIN` и неклассифицированные сообщения показаны отдельными счётчиками и не увеличивают human reply. Общее число наблюдаемых ответов и интересов также показано отдельно, чтобы не скрывать неполные данные доставки. UNKNOWN повторного касания не отменяет первоначальную доставку и не меняет знаменатель этих конверсий.
- Принятие менеджером: MANAGER_ACCEPTED среди HANDOFF_PREPARED / HANDOFF_PREPARED. Доставка пакета не заменяет ACK.
- Ошибочный интерес: INTEREST_REVIEW(correct=false) среди подтверждённых и проверенных интересов / все подтверждённые и проверенные интересы. Непроверенные случаи не считаются правильными.
- Время: медиана и максимум для interest -> handoff, handoff -> manager ACK и interest -> manager ACK; отдельно число незавершённых наблюдений. Предельный SLA пока не утверждён.
- Расходы: только переданные записи `currency=DEMO` по лидам cohort. Полная сумма и категории накапливаются точно через Decimal, не float. Стоимость делится на число квалифицированных / принятых лидов; нулевой знаменатель даёт null. Деление отображается с Decimal precision 28, точный числитель сохранён.
- В бюджет входят только явно внесённые расходы: если труд, лицензии, hosting, проверка или работа менеджера не внесены, функция их не угадывает. Нераспределённые общие расходы требуют будущего правила аллокации.
- Счётчики дублей, нарушений STOP и потери истории - `observed_count`, без гарантии отсутствия нарушений при нуле. Стабильный event ID не обязателен текущим API, поэтому повтор одинаковой записи-наблюдения не удаляется молча.
- Последний receipt выбирается по времени, при равном времени - по порядку массива. Это проекция на лид, а не полноценный учёт provider action/attempt; для реальной интеграции нужен отдельный контракт конкретных попыток.

Коммерческие targets оставлены `null/PENDING_APPROVAL`. Демо-цифры не являются прогнозом продаж.

## Источники

- [Замороженная версия центрального репозитория](https://github.com/angle-theory-studio/sales-manager-first/tree/c4964b4be87d3148a362f9a4b527ac475fca04b9)
- [Architecture decisions](https://github.com/angle-theory-studio/sales-manager-first/blob/c4964b4be87d3148a362f9a4b527ac475fca04b9/SOURCE/strativon-sales-runtime-r3-2-auto/ARCHITECTURE_DECISIONS.md)
- [Контрольное состояние](https://github.com/angle-theory-studio/sales-manager-first/blob/c4964b4be87d3148a362f9a4b527ac475fca04b9/project_records/Sales_First_Integration_Controller_State.json)
- [Make contracts: shapes и NO_OUTBOUND](https://github.com/angle-theory-studio/sales-manager-first/blob/c4964b4be87d3148a362f9a4b527ac475fca04b9/SOURCE/strativon-sales-runtime-r3-2-auto/MAKE_CONNECTOR_CONTRACTS.md)
