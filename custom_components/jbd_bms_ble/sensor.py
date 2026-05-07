import logging
from homeassistant.components.sensor import (
    SensorEntity, 
    SensorDeviceClass, 
    SensorStateClass, 
    SensorEntityDescription
)
from homeassistant.const import (
    UnitOfElectricPotential, 
    UnitOfElectricCurrent, 
    UnitOfPower, 
    PERCENTAGE, 
    UnitOfTemperature,
    UnitOfTime
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# 温度探头语义化映射表
TEMP_SENSOR_DESCRIPTIONS = {
    "mos_temp": {"name": "MOS/电池 温度", "icon": "mdi:thermometer-lines"},
    "battery_temp_1": {"name": "电池温度 1 (T1)", "icon": "mdi:thermometer"},
    "battery_temp_2": {"name": "电池温度 2 (T2)", "icon": "mdi:thermometer"},
    "battery_temp_3": {"name": "电池温度 3 (T3)", "icon": "mdi:thermometer"},
}

# 1. 基础固定传感器
BASE_SENSOR_TYPES = [
    SensorEntityDescription(key="voltage", name="总电压", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="current", name="总电流", native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="power", name="实时功率", native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="rsoc", name="剩余电量", native_unit_of_measurement=PERCENTAGE, device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="remain_capacity", name="剩余容量", native_unit_of_measurement="Ah", icon="mdi:battery-high", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="nominal_capacity", name="设计容量", native_unit_of_measurement="Ah", icon="mdi:battery-check", state_class=SensorStateClass.TOTAL),
    SensorEntityDescription(key="cycles", name="循环次数", icon="mdi:battery-sync", state_class=SensorStateClass.TOTAL),
    SensorEntityDescription(key="time_remaining", name="预计剩余时间", native_unit_of_measurement=UnitOfTime.HOURS, device_class=SensorDeviceClass.DURATION, state_class=SensorStateClass.MEASUREMENT, icon="mdi:timer-sand"),
    SensorEntityDescription(key="time_status_text", name="预计时间状态", icon="mdi:information-outline"),
    SensorEntityDescription(key="operation_status", name="运行状态", icon="mdi:information-outline"),
    SensorEntityDescription(key="errors_text", name="当前告警", icon="mdi:alert-circle-outline"),
    SensorEntityDescription(key="hardware_version", name="硬件型号", icon="mdi:chip",entity_category=EntityCategory.DIAGNOSTIC),
]

# 2. 恢复：衍生计算传感器 (压差/极值等)
CALC_SENSOR_TYPES = [
    SensorEntityDescription(key="max_cell_voltage", name="单体最大电压", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="min_cell_voltage", name="单体最小电压", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="delta_cell_voltage", name="单体最大压差", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="average_cell_voltage", name="单体平均电压", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="max_voltage_cell", name="最高电压电芯", icon="mdi:numeric-1-box-multiple-outline", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="min_voltage_cell", name="最低电压电芯", icon="mdi:numeric-2-box-multiple-outline", state_class=SensorStateClass.MEASUREMENT),
]

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data: return
    
    coordinator = data["coordinator"]
    address = data["manager"]._address
    entities = []

    # 1. 注册基础传感器
    for desc in BASE_SENSOR_TYPES:
        entities.append(JbdSensor(coordinator, desc, address))

    # 2. 注册衍生计算传感器 (压差/平均/极值)
    # 前提是 0x04 单体电压包已经被成功读取过
    if "delta_cell_voltage" in coordinator.data:
        for desc in CALC_SENSOR_TYPES:
            entities.append(JbdSensor(coordinator, desc, address))

    # 3. 动态扫描字典，识别温度探头和各个电芯电压
    for key in coordinator.data.keys():
        # 处理温度探头
        if key in TEMP_SENSOR_DESCRIPTIONS:
            info = TEMP_SENSOR_DESCRIPTIONS[key]
            desc = SensorEntityDescription(
                key=key,
                name=info["name"],
                icon=info["icon"],
                native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                device_class=SensorDeviceClass.TEMPERATURE,
                state_class=SensorStateClass.MEASUREMENT
            )
            entities.append(JbdSensor(coordinator, desc, address))
        
        # 处理单体电芯电压
        elif key.startswith("cell_") and key.endswith("_voltage"):
            num = key.split("_")[1]
            desc = SensorEntityDescription(
                key=key,
                name=f"电芯 {num} 电压",
                native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:car-battery"
            )
            entities.append(JbdSensor(coordinator, desc, address))

    async_add_entities(entities)

class JbdSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, description, address):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"jbd_{address}_{description.key}".replace(":", "")
        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, address)},
            "name": "JBD Smart BMS",
            "manufacturer": "JBD",
        }

    @property
    def native_value(self):
        if not self.coordinator.data: return None
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def available(self) -> bool:
        """如果 coordinator 整体没数据，才显示不可用"""
        return self.coordinator.last_update_success