import logging
import asyncio
import json
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from .const import CONF_UPDATE_INTERVAL

from .const import (
    DOMAIN,
    DEVICES,
    CONF_SERIALNO,
    UPDATES,
    REMOVES,
    ADD_CB,
)
from .sensor import SENSORS, MeizuBLESensor

_LOGGER = logging.getLogger(__name__)

class DeviceManager:
    """管理魅族遥控器网关的 TCP 通信 (异步优化版)"""

    def __init__(self, hass: HomeAssistant, host: str, port: int, config_entry=None):
        self._hass = hass
        self._host = host
        self._port = port
        self._config_entry = config_entry
        self._serialno = config_entry.data[CONF_SERIALNO] if config_entry else "none"
        
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._is_run = False
        self._lock = asyncio.Lock()
        self._main_task: Optional[asyncio.Task] = None

    async def open(self, start_loop: bool = False) -> Optional[dict]:
        """建立连接并获取初始化信息"""
        async with self._lock:
            try:
                # 建立异步 TCP 连接
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), 
                    timeout=5.0
                )
                _LOGGER.debug("网关[%s] Socket 已连接", self._serialno)
                
                # 获取配置信息
                config_info = await self.send_message("config_info", reply=True)
                
                if start_loop and not self._is_run:
                    self._is_run = True
                    self._main_task = self._hass.async_create_task(self._run_loop())
                
                return config_info
            except Exception as e:
                _LOGGER.error("网关[%s] 连接失败: %s", self._serialno, e)
                await self.close()
                return None

    async def close(self):
        """关闭连接并清理资源"""
        self._is_run = False
        async with self._lock:
            if self._writer:
                try:
                    self._writer.close()
                    await self._writer.wait_closed()
                except Exception:
                    pass
                self._writer = None
                self._reader = None
        
        if self._main_task:
            self._main_task.cancel()
            self._main_task = None

    async def send_message(self, msg_type: str, data: dict = None, reply: bool = False) -> Optional[dict]:
        """异步发送消息"""
        if not self._writer:
            return None

        msg_dict = {"type": msg_type}
        if data:
            msg_dict["data"] = data
        
        msg_str = json.dumps(msg_dict)
        
        try:
            self._writer.write(msg_str.encode("utf-8"))
            await self._writer.drain()
            _LOGGER.debug("网关[%s] 发送消息: %s", self._serialno, msg_str)

            if reply and self._reader:
                # 等待单条回复
                data = await asyncio.wait_for(self._reader.read(1024), timeout=3.0)
                if data:
                    return json.loads(data.decode("utf-8")).get("data")
        except Exception as e:
            _LOGGER.warning("网关[%s] 发送失败: %s", self._serialno, e)
        return None

    async def _run_loop(self):
        """主接收循环 (取代原来的 run 线程)"""
        while self._is_run:
            try:
                if not self._reader:
                    await asyncio.sleep(5)
                    await self.open(start_loop=False)
                    continue

                # 发送订阅指令
                await self.send_message("subscribe")

                while self._is_run:
                    # 使用 wait_for 实现心跳超时检测
                    try:
                        line = await asyncio.wait_for(self._reader.read(1024), timeout=30.0)
                        if not line:
                            _LOGGER.warning("网关[%s] 连接被远程关闭", self._serialno)
                            break
                        
                        msg = line.decode("utf-8")
                        self._process_message(msg)
                    
                    except asyncio.TimeoutError:
                        # 触发心跳
                        _LOGGER.debug("网关[%s] 等待超时，发送心跳", self._serialno)
                        await self.send_message("heartbeat")
                        
            except Exception as e:
                _LOGGER.error("网关[%s] 循环运行错误: %s", self._serialno, e)
                await asyncio.sleep(5) # 错误重连间隔

    def _process_message(self, msg_str: str):
        """处理接收到的消息数据"""
        try:
            # 兼容处理可能连在一起的 JSON 字符串
            if "}{" in msg_str:
                msg_str = msg_str.split("}{")[0] + "}"

            jdata = json.loads(msg_str)
            msg_type = jdata.get("type")
            data = jdata.get("data")

            if msg_type == "update":
                self._handle_update(data)
            elif msg_type == "setinterval":
                self._hass.async_create_task(
                    self._hass.config_entries.async_update_entry(
                        self._config_entry, 
                        options={CONF_UPDATE_INTERVAL: data.get("update_interval", 60)}
                    )
                )
            elif msg_type == "removebind":
                self._handle_remove(data)
            elif msg_type == "ir_learn":
                    data = jdata["data"]
                    ir_code = data.get("ircode")
                    device_addr = data.get("device")
                    
                    # 在 HA 通知栏显示学习到的红外码
                    self._hass.async_create_task(
                        self._hass.services.async_call(
                            "persistent_notification",
                            "create",
                            {
                                "title": "红外学习成功",
                                "message": f"设备 {device_addr} 学习到的代码: \n\n`{ir_code}`",
                                "notification_id": f"ir_learn_{device_addr}"
                            }
                        )
                    )
            elif msg_type == "heartbeat":
                pass # 心跳回复不需要处理
                
        except Exception as e:
            _LOGGER.error("网关[%s] 解析消息失败: %s, 原始消息: %s", self._serialno, e, msg_str)

    def _handle_update(self, data: dict):
        """处理设备数据更新"""
        device_addr = data.get("device")
        # 这里的逻辑与你之前的一致，但使用了 async_write_ha_state 的思维
        if DOMAIN in self._hass.data and self._serialno in self._hass.data[DOMAIN][DEVICES]:
            device_store = self._hass.data[DOMAIN][DEVICES][self._serialno]
            
            updates = device_store[UPDATES].get(device_addr)
            if updates:
                for callback in updates:
                    callback(data)
            elif data.get("available") == 1:
                # 发现新传感器，调用 add_cb
                self._add_sensors_sync(data)

    def _add_sensors_sync(self, init_data: dict):
        """动态添加新传感器"""
        if ADD_CB in self._hass.data[DOMAIN][DEVICES][self._serialno]:
            async_add_entities = self._hass.data[DOMAIN][DEVICES][self._serialno][ADD_CB]
            sensors = [
                MeizuBLESensor(self._hass, key, self._serialno, init_data) 
                for key in SENSORS.keys()
            ]
            self._hass.add_job(async_add_entities, sensors)

    def _handle_remove(self, data: dict):
        """处理设备移除"""
        device_addr = data.get("device")
        device_store = self._hass.data[DOMAIN][DEVICES][self._serialno]
        
        removes = device_store[REMOVES].get(device_addr)
        if removes:
            for callback in removes:
                callback()
        
        # 清理内存
        device_store[UPDATES].pop(device_addr, None)
        device_store[REMOVES].pop(device_addr, None)