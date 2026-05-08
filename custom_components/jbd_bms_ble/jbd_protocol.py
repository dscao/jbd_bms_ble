import struct
import logging

_LOGGER = logging.getLogger(__name__)

class JbdProtocol:
    @staticmethod
    def calculate_checksum(data: bytes) -> bytes:
        checksum = sum(data)
        checksum = (0x10000 - checksum) & 0xFFFF
        return struct.pack(">H", checksum)

    @staticmethod
    def build_read_command(register: int) -> bytes:
        cmd = bytearray([0xDD, 0xA5, register, 0x00])
        cmd.extend(JbdProtocol.calculate_checksum(cmd[2:4]))
        cmd.append(0x77)
        return bytes(cmd)

    @staticmethod
    def build_write_command(register: int, value) -> bytes:
        # 数据转换处理
        if isinstance(value, int):
            data = value.to_bytes(2, byteorder='big')
        else:
            data = value
            
        length = len(data)
        
        # 严格计算校验和：0x10000 - (寄存器 + 长度 + 数据字节和)
        # 注意：不要把 0x5A 或 0xDD 算进去
        payload_sum = register + length + sum(data)
        checksum = (0x10000 - payload_sum) & 0xFFFF
        
        frame = bytearray([0xDD, 0x5A, register, length])
        frame.extend(data)
        frame.extend(checksum.to_bytes(2, byteorder='big'))
        frame.append(0x77)
        
        return bytes(frame)
        
    

    @staticmethod
    def parse_balance_config(data: bytes) -> dict:
        """
        解析均衡配置寄存器 (现改为 0x52)
        通常返回 2 字节数据，大端序。
        """
        if len(data) < 2:
            return {}
        
        # 提取状态位 (通常在低字节)
        # 0x00: 关闭, 0x01: 静态均衡, 0x03: 充电均衡
        raw_val = data[1] & 0x03
        
        _LOGGER.debug("从寄存器 0x52 读取到均衡原始值: 0x%02X, 解析结果: %d", data[1], raw_val)
        return {"balance_mode_raw": raw_val}

    @staticmethod
    def build_eeprom_unlock_command() -> bytes:
        """
        构建 EEPROM 解锁指令
        向寄存器 0x00 写入 0x5678
        """
        return JbdProtocol.build_write_command(0x00, 0x5678)

    @staticmethod
    def build_eeprom_lock_command() -> bytes:
        """
        构建 EEPROM 锁定指令
        向寄存器 0x00 写入 0x0000
        """
        return JbdProtocol.build_write_command(0x00, 0x0000)

    @staticmethod
    def is_complete_packet(buffer: bytearray) -> bool:
        if not buffer or buffer[0] != 0xDD: return False
        if len(buffer) < 7: return False
        length = buffer[3]
        expected_len = 4 + length + 2 + 1 
        if len(buffer) < expected_len: return False
        return buffer[expected_len - 1] == 0x77

    @staticmethod
    def parse_basic_info(payload: bytes) -> dict:
        try:
            voltage = struct.unpack_from(">H", payload, 0)[0] * 0.01
            current = struct.unpack_from(">h", payload, 2)[0] * 0.01
            remain_cap = struct.unpack_from(">H", payload, 4)[0] * 0.01
            nominal_cap = struct.unpack_from(">H", payload, 6)[0] * 0.01
            cycles = struct.unpack_from(">H", payload, 8)[0]
            
            protection_bits = struct.unpack_from(">H", payload, 16)[0]
            rsoc = payload[19]
            
            fet_status = payload[20]
            charge_mos_on = bool(fet_status & 0x01)
            discharge_mos_on = bool(fet_status & 0x02)
            
            balance_bits_low = struct.unpack_from(">H", payload, 12)[0]
            balance_bits_high = struct.unpack_from(">H", payload, 14)[0]
            balance_bits = (balance_bits_high << 16) | balance_bits_low

            # --- 新增：生成运行状态文本 ---
            if current > 0:
                operation_status = "充电中"
            elif current < 0:
                operation_status = "放电中"
            else:
                operation_status = "待机"

            # --- 新增：生成告警信息文本 ---
            error_list = []
            if protection_bits & (1 << 0): error_list.append("单体过压")
            if protection_bits & (1 << 1): error_list.append("单体欠压")
            if protection_bits & (1 << 2): error_list.append("总过压")
            if protection_bits & (1 << 3): error_list.append("总欠压")
            if protection_bits & (1 << 4): error_list.append("充电过温")
            if protection_bits & (1 << 5): error_list.append("充电低温")
            if protection_bits & (1 << 6): error_list.append("放电过温")
            if protection_bits & (1 << 7): error_list.append("放电低温")
            if protection_bits & (1 << 8): error_list.append("充电过流")
            if protection_bits & (1 << 9): error_list.append("放电过流")
            if protection_bits & (1 << 10): error_list.append("短路")
            if protection_bits & (1 << 11): error_list.append("前端IC错误")
            if protection_bits & (1 << 12): error_list.append("软件锁定")
            
            errors_text = ", ".join(error_list) if error_list else "正常"
            
            time_remaining = 0.0
            time_status = "待机"
            
            # 设置 0.5A 阈值，防止极小电流波动导致预估时间乱跳
            if current > 0.5:  
                # 充电中：充满所需时间 = (标称容量 - 剩余容量) / 充电电流
                # 稍微加个 max 防止除以0或出现负数
                time_remaining = max(0.0, (nominal_cap - remain_cap) / current)
                time_status = "预计充满"
            elif current < -0.5: 
                # 放电中：耗尽所需时间 = 剩余容量 / 放电电流绝对值
                time_remaining = remain_cap / abs(current)
                time_status = "预计耗尽"


            result = {
                "voltage": round(voltage, 2),
                "current": round(current, 2),
                "power": round(voltage * current, 2),
                "remain_capacity": round(remain_cap, 2),
                "nominal_capacity": round(nominal_cap, 2),
                "cycles": cycles,
                "rsoc": rsoc,
                "charge_mos": charge_mos_on,
                "discharge_mos": discharge_mos_on,
                "balance_bits": balance_bits,
                # 追加到字典中供 sensor.py 读取
                "operation_status": operation_status,
                "errors_text": errors_text,
                
                # 告警保护状态位提取 (给 binary_sensor 用的)
                "err_cell_ov": bool(protection_bits & (1 << 0)),
                "err_cell_uv": bool(protection_bits & (1 << 1)),
                "err_pack_ov": bool(protection_bits & (1 << 2)),
                "err_pack_uv": bool(protection_bits & (1 << 3)),
                "err_chg_ot":  bool(protection_bits & (1 << 4)),
                "err_chg_ut":  bool(protection_bits & (1 << 5)),
                "err_dsg_ot":  bool(protection_bits & (1 << 6)),
                "err_dsg_ut":  bool(protection_bits & (1 << 7)),
                "err_chg_oc":  bool(protection_bits & (1 << 8)),
                "err_dsg_oc":  bool(protection_bits & (1 << 9)),
                "err_short":   bool(protection_bits & (1 << 10)),
                "err_ic":      bool(protection_bits & (1 << 11)),
                "err_sw_lock": bool(protection_bits & (1 << 12)),
                
                "time_remaining": round(time_remaining, 2),
                "time_status_text": time_status,
            }

            for i in range(32):
                result[f"cell_{i+1}_balancing"] = bool(balance_bits & (1 << i))
                
            # --- 优化后的温度解析 ---
            ntc_count = payload[22]
            # 建立物理意义映射表
            temp_map = {0: "mos_temp", 1: "battery_temp_1", 2: "battery_temp_2", 3: "battery_temp_3"}

            if len(payload) >= 23 + (ntc_count * 2):
                for i in range(ntc_count):
                    k_val = struct.unpack_from(">H", payload, 23 + (i * 2))[0]
                    c_val = round((k_val - 2731) / 10.0, 1)
                    
                    # 优先使用语义化键名，超出 4 个则使用通用键名
                    key = temp_map.get(i, f"temp_{i+1}")
                    result[key] = c_val
            else:
                _LOGGER.warning("温度数据包长度截断 (需 %d 字节，实收 %d 字节)，跳过温度解析", 
                                23 + (ntc_count * 2), len(payload))
                
            return result
            
        except Exception as e:
            _LOGGER.error("解析 Basic Info 失败: %s", e)
            return {}

    @staticmethod
    def parse_cell_info(payload: bytes) -> dict:
        """解析并计算单体电压信息"""
        try:
            result = {}
            cell_count = len(payload) // 2
            cell_voltages = []

            # 读取所有电芯电压
            for i in range(cell_count):
                mv = struct.unpack_from(">H", payload, i * 2)[0]
                v = round(mv / 1000.0, 3)
                cell_voltages.append(v)
                result[f"cell_{i+1}_voltage"] = v

            # --- 核心计算逻辑 (复刻 ESPHome 特性) ---
            if cell_voltages:
                max_v = max(cell_voltages)
                min_v = min(cell_voltages)
                
                result["max_cell_voltage"] = max_v
                result["min_cell_voltage"] = min_v
                result["delta_cell_voltage"] = round(max_v - min_v, 3) # 压差
                result["average_cell_voltage"] = round(sum(cell_voltages) / len(cell_voltages), 3)
                
                # 找出最大/最小电压对应的电芯编号 (1-indexed)
                # 如果有多个相同电压，index() 会返回第一个匹配的
                result["max_voltage_cell"] = cell_voltages.index(max_v) + 1
                result["min_voltage_cell"] = cell_voltages.index(min_v) + 1

            return result
        except Exception as e:
            _LOGGER.error("解析 Cell Info 失败: %s", e)
            return {}
            
    @staticmethod
    def parse_eeprom_bal(payload: bytes) -> dict:
        """解析 0x52 EEPROM 均衡配置寄存器的返回值"""
        try:
            if len(payload) >= 2:
                # 解析出 2 字节的配置值
                val = struct.unpack_from(">H", payload, 0)[0]
                return {"balance_mode_raw": val}
            return {}
        except Exception as e:
            _LOGGER.error("解析 EEPROM 0x52 失败: %s", e)
            return {}

    @staticmethod
    def parse_device_name(data: bytes) -> dict:
        """解析型号"""
        try:
            name = data.decode('ascii', errors='ignore').strip('\x00').strip()
            return {
                "hardware_version": name
            }
        except:
            return {"hardware_version": "Unknown"}