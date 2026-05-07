import logging
from homeassistant.components.binary_sensor import (
    BinarySensorEntity, BinarySensorDeviceClass, BinarySensorEntityDescription
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# JBD 保护告警位映射 (静态实体，启动时直接生成)
PROTECTION_SENSORS = (
    BinarySensorEntityDescription(key="err_cell_ov", name="单体过压保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_cell_uv", name="单体欠压保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_pack_ov", name="总过压保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_pack_uv", name="总欠压保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_chg_ot", name="充电过温保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_chg_ut", name="充电低温保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_dsg_ot", name="放电过温保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_dsg_ut", name="放电低温保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_chg_oc", name="充电过流保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_dsg_oc", name="放电过流保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_short", name="短路保护", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_ic", name="前端IC错误", device_class=BinarySensorDeviceClass.PROBLEM),
    BinarySensorEntityDescription(key="err_sw_lock", name="软件锁定", device_class=BinarySensorDeviceClass.PROBLEM),
)

async def async_setup_entry(hass, entry, async_add_entities):
    """设置 Binary Sensor 平台"""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data: return
    
    coordinator = data["coordinator"]
    address = data["manager"]._address

    entities = []

    # 1. 初始化并添加所有静态告警实体
    for desc in PROTECTION_SENSORS:
        entities.append(JbdBinarySensor(coordinator, desc, address))

    # 2. 根据实际读到的单体电压数量，生成对应数量的均衡实体
    # 计算当前字典里有多少个 cell_X_voltage
    cell_count = sum(1 for k in coordinator.data.keys() if k.startswith("cell_") and k.endswith("_voltage"))
    
    if cell_count > 0:
        _LOGGER.debug("检测到 %d 串电芯，生成对应的均衡状态实体", cell_count)
        for i in range(1, cell_count + 1):
            desc = BinarySensorEntityDescription(
                key=f"cell_{i}_balancing",
                name=f"电芯 {i} 均衡中",
                icon="mdi:battery-sync",
                entity_category=EntityCategory.DIAGNOSTIC # 设为诊断实体保持主界面整洁
            )
            entities.append(JbdBinarySensor(coordinator, desc, address))

    # 提交生成
    async_add_entities(entities)

class JbdBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """JBD 二元传感器实体 (用于保护状态和均衡状态)"""

    def __init__(self, coordinator, description, address):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"jbd_{address}_{description.key}".replace(":", "")
        self._attr_has_entity_name = True
        
        # 设定实体分类 (告警和均衡类统一归为诊断实体)
        if description.key.startswith("err_") or description.key.endswith("_balancing"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            
        self._attr_device_info = {
            "identifiers": {(DOMAIN, address)},
            "name": "JBD Smart BMS",
            "manufacturer": "JBD",
        }

    @property
    def is_on(self) -> bool | None:
        """判断是否开启/告警"""
        if not self.coordinator.data: return None
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def available(self) -> bool:
        """优雅防错：如果字典里没这个数据，显示为不可用"""
        if not self.coordinator.data: return False
        return self.entity_description.key in self.coordinator.data