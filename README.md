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

## Требования

- Home Assistant **2024.10+**
- Python **3.12+** (есть в HAOS 13+ и в Container/Core при правильной установке)
- Контроллер MyHeat с прошивкой, поддерживающей локальный веб-интерфейс (большинство современных моделей с ESP32)

---

# 📦 Установка

## Способ 1. Через HACS (рекомендуется)

### 1.1. Убедись, что у тебя установлен HACS

Если HACS ещё нет — см. https://hacs.xyz/docs/use/download/download/ (несколько минут).

### 1.2. Добавь этот репозиторий как Custom repository

1. Открой **HACS** (в боковом меню HA)
2. В правом верхнем углу нажми три точки **⋮** → **Custom repositories**
3. В появившемся диалоге заполни:
   - **Repository**: `https://github.com/Kabzbz/hass-myheat-local`
   - **Type**: **Integration**
4. Жми **ADD** → закрой диалог

### 1.3. Установи интеграцию

1. В HACS в строке поиска набери **MyHeat**
2. Кликни карточку **MyHeat (local + cloud, RU)**
3. Жми **DOWNLOAD** (правый нижний угол) → **DOWNLOAD** ещё раз для подтверждения
4. **Перезагрузи Home Assistant**:
   - Settings → System → правый верхний угол ⋮ → **Restart Home Assistant** → **Restart**

### 1.4. Добавь интеграцию

1. После рестарта: **Settings** → **Devices & Services** → **+ ADD INTEGRATION** (правый нижний угол)
2. В поиске набери **MyHeat** → кликни **MyHeat.net**
3. Откроется мастер настройки — см. раздел **«Конфигурация»** ниже

### 1.5. (Опционально) Обновления

Когда выйдет новая версия — HACS покажет уведомление, жми **UPDATE** → перезагрузка HA.

---

## Способ 2. Вручную (без HACS)

### 2.1. Скачай файлы

1. Открой https://github.com/Kabzbz/hass-myheat-local/releases/latest
2. Скачай `Source code (zip)`
3. Распакуй

### 2.2. Скопируй в HA

Скопируй папку `custom_components/myheat/` из распакованного архива в папку конфигурации HA так, чтобы получилось:

```
<HA config>/custom_components/myheat/
├── __init__.py
├── api.py
├── local_api.py
├── manifest.json
└── ...
```

Где `<HA config>`:
- **HAOS / Supervised**: на хосте по адресу `\\<HA_IP>\config\` (Samba addon) или через **File editor / Studio Code Server**
- **Container (Docker)**: то, что замаплено в `-v <path>:/config`
- **Core**: твоя папка конфигурации (обычно `~/.homeassistant/`)

### 2.3. Перезагрузи HA

Settings → System → ⋮ → **Restart Home Assistant**.

### 2.4. Добавь интеграцию

Settings → Devices & Services → + ADD INTEGRATION → найди **MyHeat.net**.

---

# ⚙️ Конфигурация (мастер настройки)

После выбора **MyHeat.net** откроется 2-3 шага.

## Шаг 1. Аккаунт и режим работы

![шаг 1: галочки локального режима](docs/step1.png)

| Поле | Что вводить |
|---|---|
| **Логин MyHeat (e-mail)** | твой e-mail на myheat.net. **Оставь пустым, если ставишь «Только локально»** |
| **API ключ** | возьми на https://my.myheat.net → клик по своему имени в правом верхнем углу → **Профиль** → раздел **API ключ** → скопировать. Оставь пустым в local-only режиме |
| **Использовать локальное подключение (резерв)** | ☑ галочка — включает hybrid режим. Опрашивается облако, при сбое автопереход на local |
| **Только локально (без облака)** | ☑ галочка — для тех, у кого вообще нет интернета. Облако не используется совсем |

**Возможные сочетания:**

| Логин/ключ | Резерв | Только локально | Что будет |
|---|---|---|---|
| есть | ☐ | ☐ | Cloud-only (как было у vooon) |
| есть | ☑ | ☐ | **Hybrid (рекомендуется)** |
| пусто | ☐ | ☑ | Local-only |
| пусто | ☑ | ☐ | ⚠ не сработает — нужны cloud-credentials для cloud-режима |

Жми **SUBMIT**.

## Шаг 2. Локальные параметры (только если включён локальный режим)

Появится, только если на шаге 1 поставлена хотя бы одна галочка.

| Поле | Default | Где взять |
|---|---|---|
| **IP-адрес или хост контроллера** | `192.168.1.50` | смотри в роутере (DHCP-клиенты, ищи устройство с производителем Espressif/MAC начинается с `ac:0b:fb`) или в приложении MyHeat → Настройки → Сеть |
| **Локальный логин** | `myheat` | заводской по умолчанию; если не менял — оставь как есть |
| **Локальный пароль** | `myheat` | заводской по умолчанию |
| **Протокол** | `http` | большинство прошивок используют http; пробуй `https` только если знаешь, что у тебя оно есть |
| **Интервал опроса в локальном режиме** | `30` сек | контроллер медленный, ниже 15 с не ставить |
| **Тайм-аут запроса** | `30` сек | если контроллер совсем тормозит (бывает «через раз») — увеличь до 60-90 |

После SUBMIT интеграция **проверит, что контроллер отвечает и логин/пароль приняты**. Если что-то не так — покажет ошибку «Не удалось достучаться до контроллера».

### Если ошибка «cannot_connect_local»

1. Открой в браузере `http://<IP контроллера>/` — должна появиться страница логина MyHeat
2. Если не открывается:
   - проверь IP: `ping <IP>` из консоли (Windows: cmd; Linux/HA: Terminal addon)
   - может, IP сменился — посмотри в роутере
   - перезагрузи контроллер по питанию (выключи автомат на 30 сек), он часто оживает после reset
   - контроллер реально тупит — попробуй через минуту ещё раз
3. Если страница открывается, но логин не пускает: попробуй стандартные `myheat`/`myheat`. Если не подходит — посмотри в приложении MyHeat настройки или сбрось контроллер к заводским

## Шаг 3. Выбор облачного устройства (только если облако включено)

| Поле | Что |
|---|---|
| **Имя** | как назвать в HA (например `MyHeat Дача`) |
| **Устройство** | выпадающий список облачных контроллеров — выбери нужный |

SUBMIT → готово.

---

# 🏠 Что появится в Home Assistant

После добавления увидишь устройство **MyHeat** в Settings → Devices & Services → MyHeat.net.

## Сущности (entity), которые точно будут

**Климат:**
- `climate.<имя>_<контур_отопления>` — для каждого heating circuit / floor / room
- `water_heater.<имя>_<гвс>` — для каждого DHW / boiler_temperature

**Температуры:**
- `sensor.<имя>_<контур>_температура` — текущая по каждому датчику
- `sensor.<имя>_<контур>_цель` — целевая
- `sensor.<имя>_котел_<...>` — flowTemp, returnTemp, pressure, modulation (модуляция только в cloud)
- `sensor.<имя>_уличная_температура` (cloud, weatherTemp)

**Состояние:**
- `sensor.<имя>_статус` (severity)
- `binary_sensor.<имя>_тревоги_активны`
- `binary_sensor.<имя>_подключение` (`dataActual` — есть ли свежие данные)
- `binary_sensor.<имя>_<котел>_горелка` — общее состояние
- `binary_sensor.<имя>_<котел>_горелка_отопление`
- `binary_sensor.<имя>_<котел>_горелка_гвс`

**Оборудование:**
- `binary_sensor.<имя>_<насос/клапан>_включено`
- `sensor.<имя>_<оборудование>_статус`

**Запрос тепла:**
- `binary_sensor.<имя>_<контур>_запрос_тепла`

## Дополнительно при включённом локальном API

- `sensor.<имя>_источник_данных` → `cloud` / `local` / `offline`
- `sensor.<имя>_баланс_sim` → ₽ из local
- `sensor.<имя>_сигнал_gsm`
- `sensor.<имя>_wifi_ssid`
- `binary_sensor.<имя>_облако_доступно` — горит когда сейчас источник=cloud
- `binary_sensor.<имя>_контроллер_интернет` — что сам прибор видит про инет

### Частота обновления локальных сенсоров

Эти 4 сенсора (**Баланс SIM**, **Сигнал GSM**, **WiFi SSID**, **Контроллер: интернет**) приходят **только из локального API** — облако таких полей не отдаёт.

- Когда **активный источник = local** (или режим «Только локально») — обновляются на каждом локальном опросе (по умолчанию 30 сек)
- Когда **активный источник = cloud** — обновляются **раз в 10 минут** фоновым лёгким запросом `/api/getState` к контроллеру. Сделано так специально, чтобы не нагружать контроллер при работе через облако
- Если локальный режим **выключен** совсем — сенсоры показывают «Неизвестно»

Все **остальные** сенсоры (температуры, давление, режимы котла, тревоги и т.п.) обновляются на каждом основном опросе — 30 сек как в cloud, так и в local-режиме.

## Примеры использования в автоматизациях

**Уведомление о смене источника на local:**
```yaml
automation:
  - alias: MyHeat ушёл на резерв
    trigger:
      - platform: state
        entity_id: sensor.myheat_источник_данных
        to: local
    action:
      - service: notify.notify
        data:
          message: "MyHeat: облако недоступно, переключился на локальный API"
```

**Уведомление о минусе на SIM:**
```yaml
automation:
  - alias: MyHeat SIM минус
    trigger:
      - platform: numeric_state
        entity_id: sensor.myheat_баланс_sim
        below: 0
    action:
      - service: notify.notify
        data:
          message: "Пополни SIM в MyHeat — баланс {{ states('sensor.myheat_баланс_sim') }} ₽"
```

**Уведомление об отсутствии интернета на контроллере:**
```yaml
automation:
  - alias: MyHeat контроллер без интернета
    trigger:
      - platform: state
        entity_id: binary_sensor.myheat_контроллер_интернет
        to: 'off'
        for: '00:05:00'
    action:
      - service: notify.notify
        data:
          message: "Контроллер MyHeat 5 минут без интернета (WiFi/GSM не отвечает)"
```

---

# 🔁 Совместимость с существующей установкой vooon

Безопасно ставить **поверх** vooon 0.6.x / 0.7.1:
- `unique_id` сущностей не менялись → `entity_id` и история **сохраняются**
- Все автоматизации и дашборды продолжат работать
- Если локальный режим не включён — поведение полностью эквивалентно upstream

**Порядок миграции с vooon:**
1. (опционально) HACS → vooon/hass-myheat → REMOVE (или оставь — не помешает, но удобнее снести)
2. HACS → Custom repositories → добавь `https://github.com/Kabzbz/hass-myheat-local` тип Integration
3. HACS → DOWNLOAD → Restart HA
4. Settings → Devices & Services → найди существующую MyHeat — **она продолжит работать как раньше**
5. Чтобы получить новые фичи — удали старую интеграцию и добавь заново, либо настрой через Options (если доступно)

---

# 🐛 Troubleshooting

## Источник данных всё время `offline`

- Проверь логи: Settings → System → Logs → search `myheat`
- Проверь облачные creds: открой `https://my.myheat.net` → залогинься тем же login/key
- Если включён локальный: проверь, что контроллер пингуется

## Источник всё время `local`, облако недоступно

- Проверь интернет на HA: Settings → System → Logs → search `connectivity`
- Проверь, что myheat.net вообще доступен: `curl -I https://my.myheat.net`
- Если у тебя ключ API устарел — перегенерируй на myheat.net и обнови интеграцию (Settings → Devices → MyHeat → ⋮ → Reconfigure, если доступно; иначе удалить и добавить заново)

## Сенсор «Баланс SIM» показывает `unknown`

- Только при `Источник данных = local` показывается
- Если ты в облачном режиме всё время — это нормально, поле берётся только из local API
- Включи `Использовать локальное подключение` в настройках интеграции

## «Контур отопления» стал water_heater

В этом форке такого быть не должно (см. фикс issue #197). Если всё-таки — посмотри в logs тип среды (`type=...`) и заведи issue в этом репо.

## Контроллер тупит, постоянно «cannot_connect_local»

Контроллер MyHeat реально вешает HTTP при перегрузке. Симптомы:
- ARP-таблица знает MAC, но TCP не отвечает
- Пинг проходит, но `curl http://<ip>/` таймаутит

Лечение:
- Подожди 1-2 минуты — обычно поднимается само
- Перезагрузи контроллер по питанию (автомат на 30 сек)
- В настройках интеграции увеличь `Тайм-аут запроса` до 60-90 секунд

## Логи для багрепорта

```
Settings → System → Logs → Download Full Log
```

Прикрепи к issue в этом репо: https://github.com/Kabzbz/hass-myheat-local/issues

---

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

### Quick install

1. HACS → ⋮ → Custom repositories → `https://github.com/Kabzbz/hass-myheat-local` → type **Integration** → Add
2. Download in HACS → Restart HA
3. Settings → Devices & Services → Add Integration → **MyHeat.net**
4. Fill in cloud creds (from https://my.myheat.net → Profile → API key) and/or tick local mode
5. If local: enter controller IP (default `192.168.1.50`), login/password (default `myheat`/`myheat`)

Default poll interval and timeout are both 30 s — the controller is slow, don't go below 15 s.

See the Russian section above for the full feature comparison, screenshots, troubleshooting and automation examples.
