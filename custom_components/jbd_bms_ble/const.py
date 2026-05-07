"""Constants for the JBD BMS Bluetooth integration."""
from homeassistant.const import Platform

DOMAIN = "jbd_bms_ble"

CONF_BLE_DEVICE_ADDRESS = "address"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_DISCONNECT_DELAY = "disconnect_delay"

DEFAULT_UPDATE_INTERVAL = 30
DEFAULT_DISCONNECT_DELAY = 10

# 追加了 BINARY_SENSOR 平台
PLATFORMS = [
    Platform.SENSOR, 
    Platform.BINARY_SENSOR, 
    Platform.SWITCH, 
    Platform.BUTTON, 
    Platform.SELECT
]

JBD_SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
JBD_CHAR_RX      = "0000ff01-0000-1000-8000-00805f9b34fb" 
JBD_CHAR_TX      = "0000ff02-0000-1000-8000-00805f9b34fb" 

REG_BASIC_INFO = 0x03
REG_CELL_INFO  = 0x04
REG_MOS_CTRL   = 0xE1
REG_BAL_CONFIG = 0x52