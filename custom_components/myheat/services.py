import logging

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import device_registry as dr

from .const import CONF_DEVICE_ID, DOMAIN
from .coordinator import MhDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def _get_coordinator(call: ServiceCall) -> MhDataUpdateCoordinator:
    hass = call.hass
    device_id = call.data[CONF_DEVICE_ID][0]
    device_registry = dr.async_get(hass)

    if (device_entry := device_registry.async_get(device_id)) is None:
        raise ValueError(f"Invalid MyHeat device id: {device_id}")

    for entry_id in device_entry.config_entries:
        if (entry := hass.config_entries.async_get_entry(entry_id)) is None:
            continue
        if entry.domain == DOMAIN:
            return entry.runtime_data

    raise ValueError(f"No controller for device id: {device_id}")


async def async_get_devices(call: ServiceCall) -> ServiceResponse:
    coordinator = await _get_coordinator(call)
    return await coordinator.api.async_get_devices()


async def async_get_device_info(call: ServiceCall) -> ServiceResponse:
    coordinator = await _get_coordinator(call)
    device_id = call.data.get("alt_device_id")
    return await coordinator.api.async_get_device_info(device_id=device_id)


async def async_set_env_goal(call: ServiceCall) -> ServiceResponse:
    coordinator = await _get_coordinator(call)
    device_id = call.data.get("alt_device_id")
    obj_id = call.data["obj_id"]
    goal = call.data["goal"]
    change_mode = call.data.get("change_mode", False)
    return await coordinator.api.async_set_env_goal(
        device_id=device_id,
        obj_id=obj_id,
        goal=goal,
        change_mode=change_mode,
    )


async def async_set_env_curve(call: ServiceCall) -> ServiceResponse:
    coordinator = await _get_coordinator(call)
    device_id = call.data.get("alt_device_id")
    obj_id = call.data["obj_id"]
    curve = call.data["curve"]
    change_mode = call.data.get("change_mode", False)
    return await coordinator.api.async_set_env_curve(
        device_id=device_id,
        obj_id=obj_id,
        curve=curve,
        change_mode=change_mode,
    )


async def async_set_eng_goal(call: ServiceCall) -> ServiceResponse:
    coordinator = await _get_coordinator(call)
    device_id = call.data.get("alt_device_id")
    obj_id = call.data["obj_id"]
    goal = call.data["goal"]
    change_mode = call.data.get("change_mode", False)
    return await coordinator.api.async_set_eng_goal(
        device_id=device_id,
        obj_id=obj_id,
        goal=goal,
        change_mode=change_mode,
    )


async def async_set_heating_mode(call: ServiceCall) -> ServiceResponse:
    coordinator = await _get_coordinator(call)
    device_id = call.data.get("alt_device_id")
    mode_id = call.data.get("mode_id")
    schedule_id = call.data.get("schedule_id")
    return await coordinator.api.async_set_heating_mode(
        device_id=device_id,
        mode_id=mode_id,
        schedule_id=schedule_id,
    )


async def async_set_security_mode(call: ServiceCall) -> ServiceResponse:
    coordinator = await _get_coordinator(call)
    device_id = call.data.get("alt_device_id")
    mode = call.data["mode"]
    return await coordinator.api.async_set_security_mode(
        device_id=device_id,
        mode=mode,
    )


async def async_refresh(call: ServiceCall) -> ServiceResponse:
    coordinator = await _get_coordinator(call)
    await coordinator.async_refresh()
    return coordinator.data


async def async_debug_data(call: ServiceCall) -> ServiceResponse:
    """Debug service to see all data structure."""
    coordinator = await _get_coordinator(call)
    
    # Логируем структуру данных для отладки
    _LOGGER.debug("=== DEBUG DATA STRUCTURE ===")
    _LOGGER.debug("Data keys: %s", list(coordinator.data.keys()) if coordinator.data else "No data")
    
    if coordinator.data:
        # Детальная информация о envs
        if 'envs' in coordinator.data:
            _LOGGER.debug("Environments (%d):", len(coordinator.data['envs']))
            for i, env in enumerate(coordinator.data['envs']):
                _LOGGER.debug("  [%d] %s (id: %s, type: %s, value: %s, target: %s)", 
                             i, env.get('name'), env.get('id'), env.get('type'), 
                             env.get('value'), env.get('target'))
        
        # Детальная информация о heaters
        if 'heaters' in coordinator.data:
            _LOGGER.debug("Heaters (%d):", len(coordinator.data['heaters']))
            for i, heater in enumerate(coordinator.data['heaters']):
                _LOGGER.debug("  [%d] %s (id: %s)", i, heater.get('name'), heater.get('id'))
                for key, value in heater.items():
                    if key not in ['name', 'id']:
                        _LOGGER.debug("    %s: %s", key, value)
        
        # Детальная информация о engs
        if 'engs' in coordinator.data:
            _LOGGER.debug("Engineering components (%d):", len(coordinator.data['engs']))
            for i, eng in enumerate(coordinator.data['engs']):
                _LOGGER.debug("  [%d] %s (id: %s, type: %s, turnedOn: %s)", 
                             i, eng.get('name'), eng.get('id'), eng.get('type'), eng.get('turnedOn'))
        
        # Все остальные ключи
        other_keys = [k for k in coordinator.data.keys() if k not in ['envs', 'heaters', 'engs', 'alarms']]
        for key in other_keys:
            value = coordinator.data[key]
            if isinstance(value, (int, float, str, bool)):
                _LOGGER.debug("%s: %s", key, value)
    
    return coordinator.data


async def async_debug_envs(call: ServiceCall) -> ServiceResponse:
    """Debug service to see only environments data."""
    coordinator = await _get_coordinator(call)
    
    envs_data = []
    if coordinator.data and 'envs' in coordinator.data:
        for env in coordinator.data['envs']:
            envs_data.append({
                'id': env.get('id'),
                'name': env.get('name'),
                'type': env.get('type'),
                'value': env.get('value'),
                'target': env.get('target'),
                'demand': env.get('demand'),
                'severity': env.get('severity'),
                'severityDesc': env.get('severityDesc')
            })
    
    # Логируем для отладки
    _LOGGER.debug("=== DEBUG ENVS ===")
    for env in envs_data:
        _LOGGER.debug("Env: %s", env)
    
    return {'environments': envs_data}


async def async_find_temperatures(call: ServiceCall) -> ServiceResponse:
    """Service to find all temperature sensors in data."""
    coordinator = await _get_coordinator(call)
    
    temperatures = []
    
    if coordinator.data:
        # Ищем в envs
        if 'envs' in coordinator.data:
            for env in coordinator.data['envs']:
                if isinstance(env.get('value'), (int, float)):
                    temperatures.append({
                        'source': 'envs',
                        'id': env.get('id'),
                        'name': env.get('name'),
                        'type': env.get('type'),
                        'value': env.get('value'),
                        'target': env.get('target')
                    })
        
        # Ищем в heaters
        if 'heaters' in coordinator.data:
            for heater in coordinator.data['heaters']:
                for key, value in heater.items():
                    if isinstance(value, (int, float)) and ('temp' in key.lower() or 'Temp' in key):
                        temperatures.append({
                            'source': 'heaters',
                            'heater_id': heater.get('id'),
                            'heater_name': heater.get('name'),
                            'key': key,
                            'value': value
                        })
        
        # Ищем в корневых данных
        for key, value in coordinator.data.items():
            if isinstance(value, (int, float)) and ('temp' in key.lower() or 'Temp' in key):
                temperatures.append({
                    'source': 'root',
                    'key': key,
                    'value': value
                })
    
    # Логируем для отладки
    _LOGGER.debug("=== FOUND TEMPERATURES ===")
    _LOGGER.debug("Total temperature sensors found: %d", len(temperatures))
    for temp in temperatures:
        _LOGGER.debug("Temp: %s", temp)
    
    return {'temperature_sensors': temperatures}


async def async_debug_full_dump(call: ServiceCall) -> ServiceResponse:
    """Полный дамп всех данных для отладки - показывает ЧТО реально приходит от API"""
    coordinator = await _get_coordinator(call)
    
    # Логируем ВСЕ данные для отладки
    _LOGGER.debug("=== FULL API RESPONSE DUMP ===")
    _LOGGER.debug("Все ключи в данных: %s", list(coordinator.data.keys()) if coordinator.data else "Нет данных")
    
    if coordinator.data:
        for key, value in coordinator.data.items():
            if isinstance(value, list):
                _LOGGER.debug("%s: [%d элементов]", key, len(value))
                for i, item in enumerate(value[:10]):  # Первые 10 элементов каждого списка
                    if isinstance(item, dict):
                        _LOGGER.debug("  [%d] %s", i, item)
                    else:
                        _LOGGER.debug("  [%d] %s", i, item)
                if len(value) > 10:
                    _LOGGER.debug("  ... и еще %d элементов", len(value) - 10)
            else:
                _LOGGER.debug("%s: %s", key, value)
    
    return coordinator.data


async def async_show_all_devices(call: ServiceCall) -> ServiceResponse:
    """Показать все устройства аккаунта - для отладки"""
    coordinator = await _get_coordinator(call)
    
    # Получаем все устройства
    devices_data = await coordinator.api.async_get_devices()
    devices = devices_data.get("devices", [])
    
    # Логируем информацию
    _LOGGER.debug("=== ВСЕ УСТРОЙСТВА АККАУНТА ===")
    _LOGGER.debug("Найдено устройств: %d", len(devices))
    
    for device in devices:
        _LOGGER.debug("Устройство: %s (ID: %s, Город: %s)", 
                     device.get("name"), device.get("id"), device.get("city"))
    
    return {"devices_count": len(devices), "devices": devices}


async def async_test_simple(call: ServiceCall) -> ServiceResponse:
    """Простой тестовый сервис"""
    _LOGGER.debug("=== ТЕСТОВЫЙ СЕРВИС ВЫЗВАН ===")
    return {"status": "test_ok", "message": "Сервис работает!"}


async def async_setup_services(hass: HomeAssistant):
    """Register all services."""
    _LOGGER.debug("Начинаем регистрацию сервисов MyHeat")
    
    hass.services.async_register(
        DOMAIN,
        "get_devices",
        async_get_devices,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "get_device_info",
        async_get_device_info,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, "set_env_goal", async_set_env_goal)
    hass.services.async_register(DOMAIN, "set_env_curve", async_set_env_curve)
    hass.services.async_register(DOMAIN, "set_eng_goal", async_set_eng_goal)
    hass.services.async_register(DOMAIN, "set_heating_mode", async_set_heating_mode)
    hass.services.async_register(DOMAIN, "set_security_mode", async_set_security_mode)
    hass.services.async_register(
        DOMAIN,
        "refresh",
        async_refresh,
        supports_response=SupportsResponse.OPTIONAL,
    )
    # Новые debug сервисы
    hass.services.async_register(
        DOMAIN,
        "debug_data",
        async_debug_data,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "debug_envs",
        async_debug_envs,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "find_temperatures",
        async_find_temperatures,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "debug_full_dump",
        async_debug_full_dump,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "show_all_devices",
        async_show_all_devices,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "test_simple",
        async_test_simple,
        supports_response=SupportsResponse.ONLY,
    )
    
    _LOGGER.debug("Все сервисы зарегистрированы")