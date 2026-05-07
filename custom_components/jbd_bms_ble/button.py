import logging
from homeassistant.components.button import (
    ButtonEntity, 
    ButtonEntityDescription, 
    ButtonDeviceClass
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BUTTON_TYPES = (
    ButtonEntityDescription(
        key="retrieve_hardware_version",
        name="获取硬件版本",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC, 
    ),
    ButtonEntityDescription(
        key="force_soc_reset",
        name="强制 SOC 重置",
        icon="mdi:battery-sync",
        device_class=ButtonDeviceClass.RESTART, # 使用重启类型的设备类
        entity_category=EntityCategory.CONFIG,  # 归类为配置实体
    ),
)

async def async_setup_entry(hass, entry, async_add_entities):
    """初始化并设置 Button 平台"""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data: 
        return
        
    coordinator = data["coordinator"]
    manager = data["manager"]
    address = manager._address

    entities = [JbdButton(coordinator, manager, desc, address) for desc in BUTTON_TYPES]
    async_add_entities(entities)


class JbdButton(CoordinatorEntity, ButtonEntity):
    """JBD BMS 原生按钮实体"""

    def __init__(self, coordinator, manager, description, address):
        super().__init__(coordinator)
        self._manager = manager
        self.entity_description = description
        self._address = address
        
        self._attr_unique_id = f"jbd_{address}_{description.key}".replace(":", "")
        self._attr_has_entity_name = True
        
        # 绑定至同一设备面板
        self._attr_device_info = {
            "identifiers": {(DOMAIN, address)},
            "name": "JBD Smart BMS",
            "manufacturer": "JBD",
        }

    async def async_press(self) -> None:
        """当用户在 HA 中点击按钮时触发"""
        action = self.entity_description.key
        _LOGGER.info("正在执行 JBD 按钮操作: %s", action)
        
        success = False

        if action == "retrieve_hardware_version":
            # 向 0x05 寄存器发送读取指令
            success = await self._manager.send_read_command(0x05) 
            
        elif action == "force_soc_reset":
            # 强制 SOC 重置：不同的 JBD 固件对应不同的校准指令
            # 常见的是向特定寄存器（如 0x5A/0x00）写入复位码，这里使用 syssi 常见的空负载做演示。
            # 如果你的板子没有重置反应，可能需要查阅你手中板子型号的具体复位 Hex 指令。
            success = await self._manager.send_command(0x00, b'\x00\x00')
            
        # 触发前端乐观更新反馈
        if success:
            _LOGGER.info("按钮操作 [%s] 指令已成功送达保护板", action)
            # 你可以强制集成刷新一次主数据，让面板数据立即更新
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("按钮操作 [%s] 执行失败，请检查蓝牙连接或设备是否支持该指令", action)