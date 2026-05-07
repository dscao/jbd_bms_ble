import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN, CONF_BLE_DEVICE_ADDRESS,
    CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL,
    CONF_DISCONNECT_DELAY, DEFAULT_DISCONNECT_DELAY
)

class JbdFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            address = user_input[CONF_BLE_DEVICE_ADDRESS].upper()
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            
            return self.async_create_entry(
                title=f"JBD BMS ({address})",
                data={CONF_BLE_DEVICE_ADDRESS: address},
                options={
                    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
                    CONF_DISCONNECT_DELAY: DEFAULT_DISCONNECT_DELAY
                }
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_BLE_DEVICE_ADDRESS): str
            }),
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return JbdOptionsFlowHandler()

class JbdOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self):
        super().__init__()

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_UPDATE_INTERVAL, 
                    default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
                ): vol.All(cv.positive_int, vol.Range(min=5, max=1800)),
                
                vol.Optional(
                    CONF_DISCONNECT_DELAY, 
                    default=options.get(CONF_DISCONNECT_DELAY, DEFAULT_DISCONNECT_DELAY)
                ): vol.All(cv.positive_int, vol.Range(min=0, max=60)),
            })
        )