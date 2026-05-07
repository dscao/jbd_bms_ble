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

from .const import JBD_CHAR_TX, JBD_CHAR_RX, REG_BASIC_INFO, REG_CELL_INFO, CONF_DISCONNECT_DELAY, DEFAULT_DISCONNECT_DELAY
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
        
        # === 核心优化：状态缓存 ===
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

    async def _async_disconnect(self, _):
        async with self._lock:
            if self._client and self._client.is_connected:
                _LOGGER.debug("读取完毕，断开连接 %s", self._address)
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
        """处理分片数据并拼装"""
        self._buffer.extend(data)
        if JbdProtocol.is_complete_packet(self._buffer):
            self._response_event.set()

    async def _get_client(self) -> Optional[BleakClient]:
        self._cancel_disconnect_timer()
        if self._client and self._client.is_connected:
            return self._client
            
        ble_device = bluetooth.async_ble_device_from_address(self._hass, self._address, connectable=True)
        if not ble_device:
            _LOGGER.error("未找到设备: %s", self._address)
            return None

        try:
            self._client = await establish_connection(
                BleakClient, ble_device, self._address,
                disconnected_callback=self._on_disconnected, max_attempts=3
            )
            await self._client.start_notify(JBD_CHAR_RX, self._notification_handler)
            return self._client
        except Exception as e:
            _LOGGER.error("连接失败: %s", e)
            return None

    # --- 通用发送与等待逻辑 ---
    async def _send_and_wait(self, client, register_address, timeout=5.0) -> bytes | None:
        self._buffer.clear()
        self._response_event.clear()
        cmd = JbdProtocol.build_read_command(register_address)
        
        try:
            await client.write_gatt_char(JBD_CHAR_TX, cmd, response=False)
        except Exception:
            await client.write_gatt_char(JBD_CHAR_TX, cmd, response=True)
        
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
            if len(self._buffer) > 4 and self._buffer[1] == register_address:
                length = self._buffer[3]
                return bytes(self._buffer[4 : 4 + length])
        except asyncio.TimeoutError:
            pass
        return None

    # --- 获取数据的主方法 ---
    async def fetch_bms_data(self) -> dict:
        client = await self._get_client()
        if not client: 
            # 优雅报错：如果连不上蓝牙，告诉 Coordinator 失败了，HA 会保留上次状态不闪烁
            raise UpdateFailed("无法连接到 JBD 保护板蓝牙")

        read_success = False

        async with self._lock:
            try:
                # ================= 1. 读取 0x03 基础信息 =================
                payload_03 = await self._send_and_wait(client, REG_BASIC_INFO)
                if payload_03:
                    # 读取成功，更新到缓存中
                    self._state_cache.update(JbdProtocol.parse_basic_info(payload_03))
                    read_success = True
                else:
                    _LOGGER.debug("0x03 漏包，将使用缓存值")

                # ================= 2. 读取 0x04 单体电压 =================
                await asyncio.sleep(0.2) # 增加 200ms 延迟，防止两条指令冲撞
                payload_04 = await self._send_and_wait(client, REG_CELL_INFO)
                if payload_04:
                    self._state_cache.update(JbdProtocol.parse_cell_info(payload_04))
                    read_success = True
                else:
                    _LOGGER.debug("0x04 漏包，将使用缓存值")

                # ================= 3. 读取 EEPROM 0x52 (仅首次) =================
                if not self._eeprom_read_done:
                    await asyncio.sleep(0.2)
                    payload_52 = await self._send_and_wait(client, 0x52)
                    if payload_52:
                        self._state_cache.update(JbdProtocol.parse_eeprom_bal(payload_52))
                        self._eeprom_read_done = True

                # 如果这次连接什么都没读到，且缓存也是空的，才抛出异常
                if not read_success and not self._state_cache:
                    raise UpdateFailed("所有寄存器读取超时")

                # 永远返回缓存的完整字典
                return self._state_cache
                
            except Exception as e:
                _LOGGER.error("数据异常: %s", e)
                raise UpdateFailed(f"数据读取异常: {e}")
            finally:
                self._schedule_disconnect()

    async def send_read_command(self, register_address: int) -> bool:
        """执行手动读取并更新缓存"""
        client = await self._get_client()
        if not client: return False

        async with self._lock:
            try:
                # 1. 调用底层收发方法
                payload = await self._send_and_wait(client, register_address, timeout=5.0)
                
                if payload:
                    # 2. 根据寄存器地址进行解析并存入缓存
                    if register_address == 0x05:
                        parsed_data = JbdProtocol.parse_hardware_version(payload)
                        self._state_cache.update(parsed_data) # 关键：存入缓存
                        _LOGGER.info("成功解析硬件版本: %s", parsed_data.get("hardware_version"))
                        
                    elif register_address == 0x11:
                        parsed_data = JbdProtocol.parse_error_counts(payload)
                        self._state_cache.update(parsed_data) # 关键：存入缓存
                        _LOGGER.info("成功解析历史错误统计数据")
                    
                    # 3. 立即通知 Coordinator 数据已更新
                    # 这样不需要等下一次自动轮询，界面会立刻刷新
                    return True
                return False
            except Exception as e:
                _LOGGER.error("手动读取异常: %s", e)
                return False
            finally:
                self._schedule_disconnect()

    async def send_command(self, register: int, data: bytes) -> bool:
        """
        向 BMS 发送控制(写入)指令 (供开关、重置 SOC 使用)
        """
        client = await self._get_client()
        if not client: 
            return False

        cmd = JbdProtocol.build_write_command(register, data)
        async with self._lock:
            try:
                _LOGGER.debug("向 JBD 发送写入指令 (Reg: 0x%02X): %s", register, cmd.hex().upper())
                # 写入控制指令，等待蓝牙底层响应确认
                await client.write_gatt_char(JBD_CHAR_TX, cmd, response=True)
                return True
            except Exception as e:
                _LOGGER.error("发送控制指令失败: %s", e)
                return False
            finally:
                self._schedule_disconnect()