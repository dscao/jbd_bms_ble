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
        key="retrieve_device_name",
        name="获取硬件型号",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC, 
    ),
    ButtonEntityDescription(
        key="restart_bms",
        name="重启 BMS",
        icon="mdi:restart",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG, 
    ),
)

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data: return
    
    coordinator = data["coordinator"]
    manager = data["manager"]
    address = manager._address

    entities = [JbdButton(coordinator, manager, desc, address) for desc in BUTTON_TYPES]
    async_add_entities(entities)

class JbdButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, manager, description, address):
        super().__init__(coordinator)
        self._manager = manager
        self.entity_description = description
        self._address = address
        
        self._attr_unique_id = f"jbd_{address}_{description.key}".replace(":", "")
        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, address)},
            "name": "JBD Smart BMS",
            "manufacturer": "JBD",
        }

    async def async_press(self) -> None:
        action = self.entity_description.key
        _LOGGER.info("执行操作: %s", self.entity_description.name)
        
        success = False

        if action == "retrieve_device_name":
            # 向 0x05 发送读取指令 (获取硬件型号/名称)
            success = await self._manager.send_read_command(0x05) 
            
        elif action == "restart_bms":
            # 执行重启指令 (解锁 -> 写入重启码 -> 锁定)
            success = await self._manager.send_command(0xDD, b'\x00\x1D')
            
        if success:
            _LOGGER.info("%s 指令执行成功", self.entity_description.name)
            # 关键：手动读取成功后，必须让 Coordinator 强制更新数据到实体
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("%s 执行失败，请检查蓝牙连接", self.entity_description.name)