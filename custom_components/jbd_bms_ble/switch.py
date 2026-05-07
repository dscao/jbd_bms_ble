import logging
import struct
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, REG_MOS_CTRL

_LOGGER = logging.getLogger(__name__)

SWITCH_TYPES = (
    SwitchEntityDescription(key="charge_mos", name="充电开关", icon="mdi:battery-charging"),
    SwitchEntityDescription(key="discharge_mos", name="放电开关", icon="mdi:battery-minus"),
)

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data: return
    
    coordinator = data["coordinator"]
    manager = data["manager"]

    entities = [JbdMosSwitch(coordinator, manager, desc) for desc in SWITCH_TYPES]
    async_add_entities(entities)

class JbdMosSwitch(CoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator, manager, description):
        super().__init__(coordinator)
        self._manager = manager
        self.entity_description = description
        self._address = manager._address
        
        self._attr_unique_id = f"jbd_{self._address}_{description.key}".replace(":", "")
        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._address)},
            "name": "JBD Smart BMS",
            "manufacturer": "JBD",
        }

    @property
    def is_on(self):
        """读取状态，依赖 protocol 中解析出的真实状态"""
        if not self.coordinator.data: return None
        return self.coordinator.data.get(self.entity_description.key)

    async def _update_mos_state(self, turn_on: bool):
        """核心计算逻辑"""
        # 读取当前两个开关的期望状态
        curr_charge = self.coordinator.data.get("charge_mos", True)
        curr_discharge = self.coordinator.data.get("discharge_mos", True)

        # 赋予新状态
        if self.entity_description.key == "charge_mos":
            curr_charge = turn_on
        else:
            curr_discharge = turn_on

        # JBD MOS Override 计算
        override_val = 0
        if not curr_charge:   override_val |= 0x01
        if not curr_discharge: override_val |= 0x02

        # 发送 2 字节的数据 (大端序)
        payload = struct.pack(">H", override_val)
        success = await self._manager.send_command(REG_MOS_CTRL, payload)

        if success:
            # 乐观更新本地 UI
            self.coordinator.data[self.entity_description.key] = turn_on
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._update_mos_state(True)

    async def async_turn_off(self, **kwargs):
        await self._update_mos_state(False)