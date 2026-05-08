import logging
import asyncio
from typing import Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import UpdateFailed
from bleak import BleakClient
from bleak_retry_connector import establish_connection

from .const import JBD_CHAR_TX, JBD_CHAR_RX, REG_BASIC_INFO, REG_CELL_INFO, CONF_DISCONNECT_DELAY, DEFAULT_DISCONNECT_DELAY, REG_BAL_CONFIG
from .jbd_protocol import JbdProtocol

_LOGGER = logging.getLogger(__name__)

class JbdBLEManager:
    def __init__(self, hass: HomeAssistant, address: str, config_entry: ConfigEntry):
        self._hass = hass
        self._address = address
        self._config_entry = config_entry
        self._client: Optional[BleakClient] = None
        self._lock = asyncio.Lock()
        self._disconnect_timer = None
        
        self._buffer = bytearray()
        self._response_event = asyncio.Event()
        self._eeprom_read_done = False  
        self._state_cache = {} 

    def _cancel_disconnect_timer(self):
        if self._disconnect_timer:
            self._disconnect_timer()
            self._disconnect_timer = None

    def _schedule_disconnect(self):
        self._cancel_disconnect_timer()
        delay = self._config_entry.options.get(CONF_DISCONNECT_DELAY, DEFAULT_DISCONNECT_DELAY)
        if delay > 0:
            self._disconnect_timer = async_call_later(
                self._hass, delay, self._async_disconnect
            )

    async def _async_disconnect(self, _=None):
        async with self._lock:
            if self._client and self._client.is_connected:
                _LOGGER.debug("读取任务结束，释放连接: %s", self._address)
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
            self._client = None
            self._disconnect_timer = None

    @callback
    def _on_disconnected(self, client):
        self._client = None
        self._cancel_disconnect_timer()
        self._response_event.set()

    def _notification_handler(self, sender, data: bytes):
        self._buffer.extend(data)
        if JbdProtocol.is_complete_packet(self._buffer):
            self._response_event.set()

    async def _get_client(self) -> Optional[BleakClient]:
        self._cancel_disconnect_timer()
        if self._client and self._client.is_connected:
            return self._client
            
        ble_device = bluetooth.async_ble_device_from_address(self._hass, self._address, connectable=True)
        if not ble_device:
            _LOGGER.error("未发现蓝牙代理设备: %s", self._address)
            return None

        try:
            self._client = await establish_connection(
                BleakClient, ble_device, self._address,
                disconnected_callback=self._on_disconnected, max_attempts=3
            )
            await self._client.start_notify(JBD_CHAR_RX, self._notification_handler)
            return self._client
        except Exception as e:
            _LOGGER.error("连接握手失败: %s", e)
            return None

    # --- 底层收发逻辑 ---
    async def _send_and_wait(self, client, register_address, timeout=5.0) -> bytes | None:
        self._buffer.clear()
        self._response_event.clear()
        cmd = JbdProtocol.build_read_command(register_address)
        
        try:
            # 读指令不需要 response=True 响应
            await client.write_gatt_char(JBD_CHAR_TX, cmd, response=False)
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
            
            # 判断包头是否正确
            if len(self._buffer) >= 4 and self._buffer[1] == register_address:
                 length = self._buffer[3]
                 return bytes(self._buffer[4 : 4 + length])
        except Exception:
            pass
        return None

    async def fetch_bms_data(self) -> dict:
        async with self._lock:
            client = await self._get_client()
            if not client: raise UpdateFailed("蓝牙未连接")

            try:
                # 读取 0x03
                p03 = await self._send_and_wait(client, REG_BASIC_INFO)
                if p03: self._state_cache.update(JbdProtocol.parse_basic_info(p03))
                
                await asyncio.sleep(0.3) # 稍微加大延时
                
                # 读取 0x04
                p04 = await self._send_and_wait(client, REG_CELL_INFO)
                if p04: self._state_cache.update(JbdProtocol.parse_cell_info(p04))

                # 3. 首次进入或未成功时，同步 0x52 均衡配置
                if not self._eeprom_read_done:
                    _LOGGER.debug("正在从 0x52 同步均衡配置...")
                    p52 = await self._send_and_wait(client, REG_BAL_CONFIG)
                    if p52:
                        self._state_cache.update(JbdProtocol.parse_balance_config(p52))
                        self._eeprom_read_done = True
                        _LOGGER.info("均衡配置同步成功 (来自 0x52)")
                
                return self._state_cache
            except Exception as e:
                raise UpdateFailed(f"通讯链路异常: {e}")
            finally:
                self._schedule_disconnect()

    async def send_read_command(self, register_address: int) -> bool:
        """执行手动读取并更新缓存"""
        async with self._lock:
            client = await self._get_client()
            if not client: return False

            try:
                # 增加超时，型号字符串较长
                payload = await self._send_and_wait(client, register_address, timeout=6.0)
                
                if payload:
                    # 如果是读取硬件型号 (0x05)
                    if register_address == 0x05:
                        # 确保 JbdProtocol.parse_device_name 存在，或改用你 protocol.py 里的函数名
                        name_data = JbdProtocol.parse_device_name(payload)
                        _LOGGER.info("成功获取硬件型号: %s", name_data)
                        self._state_cache.update(name_data)
                        return True
                return False
            except Exception as e:
                _LOGGER.error("手动读取异常: %s", e)
                return False
            finally:
                self._schedule_disconnect()

    # --- 核心控制方法 (全自动授权版) ---
    async def send_command(self, register: int, data: bytes) -> bool:
        async with self._lock:
            client = await self._get_client()
            if not client: return False

            try:
                # SRAM 寄存器(如 0xE1) 直接写；EEPROM 寄存器(如 0x2D) 自动三步走
                if register == 0xE1:
                    _LOGGER.info("正在下发 MOS 直接控制指令...")
                    cmd = JbdProtocol.build_write_command(register, data)
                    await client.write_gatt_char(JBD_CHAR_TX, cmd, response=False)
                    await asyncio.sleep(0.5)
                else:
                    # 针对 0x52 等 EEPROM 寄存器，执行 [解锁 -> 写入 -> 锁定]
                    _LOGGER.info("正在修改配置寄存器 0x%02X...", register)
                    # 解锁
                    await client.write_gatt_char(JBD_CHAR_TX, JbdProtocol.build_write_command(0x00, 0x5678), response=True)
                    await asyncio.sleep(0.6)
                    # 写入实际数据 (此处将下发到 0x52)
                    await client.write_gatt_char(JBD_CHAR_TX, JbdProtocol.build_write_command(register, data), response=True)
                    await asyncio.sleep(0.6)
                    # 锁定
                    await client.write_gatt_char(JBD_CHAR_TX, JbdProtocol.build_write_command(0x00, 0x0000), response=True)
                    
                    # 修改完 0x52 后，标记需要重新同步
                    if register == REG_BAL_CONFIG:
                        self._eeprom_read_done = False
                    
                return True
            except Exception as e:
                _LOGGER.error("控制序列发送失败: %s", e)
                return False
            finally:
                self._schedule_disconnect()