import logging
import struct
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, REG_BAL_CONFIG

_LOGGER = logging.getLogger(__name__)

# 定义下拉菜单的选项与底层十六进制数值的映射
# (具体的 Hex 值请根据你的 JBD 固件手册或 syssi 源码进行替换)
# 这里假设：0x00=关闭，0x03=仅充电均衡(开启+仅充电)，0x01=静态均衡(开启+不限充电)
OPTIONS_MAP = {
    "关闭": 0x00,
    "充电均衡": 0x03, 
    "静态均衡": 0x01,
}

# 反向映射，用于从数值还原文本
REVERSE_MAP = {v: k for k, v in OPTIONS_MAP.items()}

# 实体描述
SELECT_TYPES = (
    SelectEntityDescription(
        key="balance_mode",
        name="均衡模式设置",
        icon="mdi:tune-variant", # 使用调节/设置的图标
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
        """
        获取当前选中的选项。
        如果 coordinator.data 中有读取到的值，则显示；
        如果没有（比如没去读 EEPROM），则默认显示 "关闭" 或上次缓存的值。
        """
        if not self.coordinator.data: 
            return None
            
        # 假设在 parse_basic_info 中我们将这个数值存入了 "balance_mode_raw"
        raw_val = self.coordinator.data.get("balance_mode_raw")
        
        # 如果能在映射表里找到对应的文字，就返回文字；否则返回默认值
        if raw_val is not None and raw_val in REVERSE_MAP:
            return REVERSE_MAP[raw_val]
            
        # 乐观更新的缓存回显（如果刚点击了下拉菜单，后台还没回传，直接显示缓存）
        return self.coordinator.data.get(self.entity_description.key, "静态均衡")

    async def async_select_option(self, option: str) -> None:
        """
        当用户在 HA 面板中点击下拉菜单并选择了一个选项时触发
        """
        # 1. 查找对应的十六进制数值
        val = OPTIONS_MAP.get(option)
        if val is None:
            _LOGGER.error("无效的选项: %s", option)
            return
            
        _LOGGER.info("准备将均衡模式设置为: %s (数值: %s)", option, hex(val))
        
        # 2. 将数值打包为 2 字节 (大部分 JBD 参数是 16位大端序)
        payload = struct.pack(">H", val)
        
        # 3. 通过蓝牙代理发送控制指令 (调用你之前在 ble_manager.py 中写的 send_command 方法)
        success = await self._manager.send_command(REG_BAL_CONFIG, payload)
        
        # 4. 乐观更新机制 (Optimistic Update)
        # 如果发送指令没报错，我们假设板子已经收到了，直接在 HA 前端更新状态，
        # 而不需要等待下一次蓝牙长轮询，让用户感觉“点击即生效”，非常丝滑。
        if success:
            _LOGGER.debug("指令下发成功，更新本地状态")
            # 将选择的文字直接存入 coordinator 字典
            self.coordinator.data[self.entity_description.key] = option
            # 强制刷新 Home Assistant 前端 UI
            self.async_write_ha_state()
        else:
            _LOGGER.error("设置均衡模式失败，请检查蓝牙连接")