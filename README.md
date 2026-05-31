# MyHeat for Home Assistant — local + cloud (RU fork)

Форк [vooon/hass-myheat](https://github.com/vooon/hass-myheat) с **локальным API**, **hybrid cloud/local fallback**, **русским UI** и множеством дополнительных сенсоров.

> 🇬🇧 English summary below.

---

## Зачем этот форк?

Базовая интеграция от vooon ходит **только в облако** `api.myheat.net`. Когда:
- пропадает интернет,
- упал облачный сервис MyHeat,
- баланс GSM кончился, и контроллер перешёл на резерв без связи,

— интеграция перестаёт обновлять данные и теряет управление котлом.

Этот форк добавляет **локальный HTTP API** к контроллеру (порт 80, по адресу типа `192.168.1.50`):

- **Hybrid режим** (рекомендуется): cloud-first → при сбое автоматически переключается на local → когда облако оживает, возвращается обратно.
- **Local-only режим**: только локальный API, облако вообще не дёргается. Для тех, у кого нет интернета вообще.
- **Cloud-only режим**: как было у vooon, для совместимости.

В UI отдельный сенсор показывает текущий источник данных (`cloud` / `local` / `offline`).

## Что есть сверх апстрима

| Фича | vooon 0.7.1 | Здесь |
|---|---|---|
| Hybrid cloud/local fallback | ❌ | ✅ |
| Local-only режим (без облака) | ❌ | ✅ |
| HTTPS для local | ❌ | ✅ |
| Отдельный poll-интервал для local | ❌ | ✅ настраивается (15-600 с) |
| Сенсор «Источник данных» | ❌ | ✅ `cloud` / `local` / `offline` |
| Бинарный «Облако доступно» | ❌ | ✅ |
| Бинарный «Контроллер: интернет» | ❌ | ✅ что сам прибор видит |
| Сенсор «Баланс SIM» в ₽ | ❌ | ✅ из local API, работает без облака |
| Сенсор «Сигнал GSM» | ❌ | ✅ |
| Сенсор «WiFi SSID» контроллера | ❌ | ✅ |
| Сенсор тревог `Alarms` | ❌ | ✅ |
| Комбинированный бинарный «Горелка» | ❌ | ✅ |
| Сенсор `Demand` для всех типов сред | ❌ | ✅ |
| Дополнительные температурные сенсоры | ❌ | ✅ |
| Расширенный `device_info` (sw_version, serial) | ❌ | ✅ |
| Динамические диапазоны t° по типу среды (бойлер 20-60, пол 15-45, контур 20-85, комната 5-30) | ❌ статично | ✅ |
| Русские названия и атрибуты | ❌ англ. | ✅ |
| Расширенный `services.py` (~350 строк vs ~140) | ❌ | ✅ |
| `floor_temperature` → climate (issue #197 в upstream) | ❌ багует | ✅ исправлено |
| Платформа `water_heater` для бойлеров ГВС | ✅ (0.7.1) | ✅ + точный фильтр только на `boiler_temperature` и `dhw_temperature` |

## Сравнение с vooon 0.9.0

Vooon выпустил v0.9.0 (31 мая 2026) с заявкой «Add local API support», но в коде **минимум 4 синтаксические ошибки Python 2** (`except T, V:` вместо `except (T, V):`) — релиз **не загружается** ни в одной версии HA. Все полезные идеи оттуда (HTTPS, отдельный poll-интервал, `floor_temperature`) портированы сюда в работающем виде.

## Требования

- Home Assistant **2024.10+**
- Python **3.12+**
- Контроллер MyHeat с прошивкой, поддерживающей локальный веб-интерфейс (большинство современных моделей с ESP32)

## Установка

### Через HACS (рекомендуется)

1. HACS → ⋮ → **Custom repositories**
2. Repository: `https://github.com/Kabzbz/hass-myheat-local`
3. Type: **Integration** → Add
4. Найди **MyHeat (local + cloud, RU)** в списке HACS → Install
5. Перезагрузи Home Assistant
6. Settings → Devices & Services → Add Integration → **MyHeat.net**

### Вручную

1. Скачай содержимое `custom_components/myheat/` из этого репо
2. Скопируй в `<HA config>/custom_components/myheat/`
3. Перезагрузи HA
4. Settings → Devices & Services → Add Integration → **MyHeat.net**

## Настройка

### Шаг 1: облако + галочки локального

| Поле | Описание |
|---|---|
| Логин MyHeat (e-mail) | для облачного API (можно оставить пустым, если выбран «только локально») |
| API ключ | возьми на https://my.myheat.net → Настройки → Профиль |
| Использовать локальное подключение (резерв) | ✅ — включает fallback на local при сбое облака |
| Только локально (без облака) | ✅ — для установки совсем без интернета |

### Шаг 2: локальные параметры (если включено)

| Поле | Default | Описание |
|---|---|---|
| IP-адрес или хост контроллера | `192.168.1.50` | смотри в роутере / в приложении MyHeat |
| Локальный логин | `myheat` | заводской по умолчанию |
| Локальный пароль | `myheat` | заводской по умолчанию |
| Протокол | `http` | большинство прошивок используют http |
| Интервал опроса в локальном режиме | 30 с | контроллер медленный, ниже 15 с не ставить |
| Тайм-аут запроса | 30 с | если контроллер тормозит — увеличить |

### Шаг 3: выбор облачного устройства (если не «только локально»)

Стандартный шаг как в upstream.

## Что появится в HA

При включённом local API дополнительно к стандартному набору сущностей:

- `sensor.<имя>_источник_данных` — `cloud` / `local` / `offline`
- `sensor.<имя>_баланс_sim` — баланс ₽ из local
- `sensor.<имя>_сигнал_gsm`
- `sensor.<имя>_wifi_ssid`
- `binary_sensor.<имя>_облако_доступно` — горит когда сейчас источник=cloud
- `binary_sensor.<имя>_контроллер_интернет` — что сам прибор видит про инет

## Совместимость с существующей установкой vooon

Безопасно ставить **поверх** vooon 0.6.x / 0.7.1:
- `unique_id` сущностей не менялись → entity_id и история **сохраняются**
- Все ваши автоматизации и дашборды продолжат работать
- Если локальный режим не включён — поведение полностью эквивалентно upstream

⚠️ После установки `0.9.0` от vooon (если ставили) — снесите его, он не запускался. Эту интеграцию можно ставить с нуля или поверх 0.6.x / 0.7.1.

## Команды, не поддерживаемые в local-only режиме

Все 5 управляющих методов API (setEnvGoal, setEnvCurve, setEngGoal, setHeatingMode, setSecurityMode) имеют локальные эквиваленты через `setObjState` — работают и в local-only. Если в будущей версии добавятся cloud-only команды, в local-режиме они вернут ошибку «В локальном режиме не поддерживается».

## Известные ограничения local API

Локальный API контроллера **не отдаёт**:
- % модуляции горелки (cloud отдаёт)
- Историю / графики
- Уличную температуру и название города

Эти поля в local-режиме заполняются нулями / `None`.

## Credits

- Базовая интеграция: [@vooon](https://github.com/vooon) ([hass-myheat](https://github.com/vooon/hass-myheat)) — MIT
- Локальный API, расширения, русификация: [@Kabzbz](https://github.com/Kabzbz)
- Reverse-engineering локального API контроллера: исследование веб-интерфейса прибора + официальная PDF документация MyHeat

## Лицензия

MIT — см. [LICENSE](LICENSE).

---

## English summary

Fork of [vooon/hass-myheat](https://github.com/vooon/hass-myheat) adding:

- **Local HTTP API** to the controller (`POST /api/login`, `getState`, `getObjState`, `setObjState` over port 80)
- **Hybrid cloud/local fallback** — automatically switches to local when cloud is down, switches back when it recovers
- **Local-only mode** for installs without internet
- **HTTPS support**, **configurable per-source poll intervals**, **configurable timeouts**
- **`floor_temperature` → climate** fix (upstream issue #197)
- **Source/availability sensors**: active data source, cloud reachability, controller's internet status, GSM balance/signal, WiFi SSID
- **Russian UI** and entity attributes
- Many extra sensors (alarms, combined burner, demand for all env types, additional temperature sensors)

Install via HACS custom repository: `https://github.com/Kabzbz/hass-myheat-local`.

See the Russian section above for full feature comparison and configuration walkthrough.
