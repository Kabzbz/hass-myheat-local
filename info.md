# MyHeat (local + cloud, RU)

Форк [vooon/hass-myheat](https://github.com/vooon/hass-myheat) с:

- **Локальным HTTP API** к контроллеру (читает напрямую с `http://<ip>/api/...`)
- **Hybrid режимом**: cloud-first → автоматический fallback на local при потере связи
- **Local-only режимом** — для тех, у кого вообще нет интернета
- **Русским UI и сенсорами** (Баланс SIM, Сигнал GSM, WiFi SSID, Источник данных, и т.п.)
- **Расширенным набором entity** (Severity, Demand, комбинированная горелка, Alarms)
- **Багфиксом** для `floor_temperature` / heating circuits (issue #197 в upstream)

См. [README](README.md) для подробной таблицы отличий и инструкции по установке.
