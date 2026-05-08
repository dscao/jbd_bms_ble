import logging
import struct
import asyncio
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, REG_BAL_CONFIG

_LOGGER = logging.getLogger(__name__)

OPTIONS_MAP = {
    "关闭": 0x00,
    "静态均衡": 0x01,
    "充电均衡": 0x03, 
}
REVERSE_MAP = {v: k for k, v in OPTIONS_MAP.items()}

# 反向映射，用于从数值还原文本
REVERSE_MAP = {v: k for k, v in OPTIONS_MAP.items()}

# 实体描述
SELECT_TYPES = (
    SelectEntityDescription(
        key="balance_mode",
        name="均衡模式设置",
        icon="mdi:tune-variant",
    ),
)

async def async_setup_entry(hass, entry, async_add_entities):
    """初始化 Select 平台"""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data: return
    
    coordinator = data["coordinator"]
    manager = data["manager"]
    address = manager._address

    entities = [JbdSelect(coordinator, manager, desc, address) for desc in SELECT_TYPES]
    async_add_entities(entities)


class JbdSelect(CoordinatorEntity, SelectEntity):
    """JBD BMS 下拉选择实体"""

    def __init__(self, coordinator, manager, description, address):
        super().__init__(coordinator)
        self._manager = manager
        self.entity_description = description
        self._address = address
        
        # 提取选项文本列表供 HA 前端显示
        self._attr_options = list(OPTIONS_MAP.keys())
        
        self._attr_unique_id = f"jbd_{address}_{description.key}".replace(":", "")
        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, address)},
            "name": "JBD Smart BMS",
            "manufacturer": "JBD",
        }

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.data: return None
        
        # 优先读取同步回来的原始数值
        raw_val = self.coordinator.data.get("balance_mode_raw")
        
        # 映射逻辑必须与 OPTIONS_MAP 严丝合缝
        if raw_val == 0x01: return "静态均衡"
        if raw_val == 0x03: return "充电均衡"
        if raw_val == 0x00: return "关闭"
            
        # 如果 raw_val 为 None (没读到)，回显缓存值，默认“关闭”
        return self.coordinator.data.get(self.entity_description.key, "关闭")

    async def async_select_option(self, option: str) -> None:
        val = OPTIONS_MAP.get(option)
        if val is None: return
            
        _LOGGER.info("设置均衡模式: %s", option)
        payload = struct.pack(">H", val)
        
        # 0x2D 属于 EEPROM，BleManager 里的 register < 0xA0 逻辑会自动帮它解锁
        success = await self._manager.send_command(REG_BAL_CONFIG, payload)
        
        if success:
            self.coordinator.data[self.entity_description.key] = option
            self.async_write_ha_state()