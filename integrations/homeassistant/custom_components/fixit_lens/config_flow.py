"""Set up with the Fix-It Lens address and a token the household made for its Home Assistant: typed in, or handed over
by the Fix-It Lens add-on through the Supervisor's discovery (nothing to paste)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import CONF_TOKEN, CONF_URL, DEFAULT_TITLE, DOMAIN
from .core import AuthFailed, Client, Found, Unreachable, home_name
from .discovery import CONF_DEVICES, add_ticked, known, schema

SCHEMA = vol.Schema({vol.Required(CONF_URL): str, vol.Required(CONF_TOKEN): str})


def home_title(household: dict[str, Any]) -> str:
    """The entry is named after the home, so two homes read apart: "Demo home"."""
    return home_name(household) or DEFAULT_TITLE


class FixitConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _discovered: dict[str, str]
    _title: str
    _found: list[Found]

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DiscoverFlow()

    async def _check(self, url: str, token: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
        client = Client(url, token, http=get_async_client(self.hass))
        try:
            return await client.household(), {}
        except AuthFailed:
            return None, {"base": "invalid_auth"}
        except Unreachable:
            return None, {"base": "cannot_connect"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            household, errors = await self._check(user_input[CONF_URL], user_input[CONF_TOKEN])
            if household is not None:
                await self.async_set_unique_id(str(household.get("id")))
                self._abort_if_unique_id_configured()
                self._discovered = {CONF_URL: user_input[CONF_URL], CONF_TOKEN: user_input[CONF_TOKEN]}
                self._title = home_title(household)
                return await self.async_step_discover()
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The add-on made a key for one of its homes; a home already set up here takes the new address and key."""
        found = {CONF_URL: str(discovery_info.config.get(CONF_URL, "")),
                 CONF_TOKEN: str(discovery_info.config.get(CONF_TOKEN, ""))}
        household, errors = await self._check(found[CONF_URL], found[CONF_TOKEN])
        if household is None:
            return self.async_abort(reason=errors["base"])
        await self.async_set_unique_id(str(household.get("id")))
        self._abort_if_unique_id_configured(updates=found)
        self._discovered = found
        self._title = home_title(household)
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_discover()
        return self.async_show_form(step_id="hassio_confirm")

    async def async_step_discover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The end of setup: the appliances Home Assistant knows, to tick; none ticked adds none."""
        client = Client(self._discovered[CONF_URL], self._discovered[CONF_TOKEN], http=get_async_client(self.hass))
        if user_input is None:
            try:
                self._found = known(self.hass, await client.appliances())
            except (AuthFailed, Unreachable):
                self._found = []
            if not self._found:
                return self.async_create_entry(title=self._title, data=self._discovered)
            return self.async_show_form(step_id="discover", data_schema=schema(self._found))
        added, errors, why = await add_ticked(client, self._found, user_input.get(CONF_DEVICES, []))
        if errors:
            self._found = [f for f in self._found if f.device_id not in added]
            return self.async_show_form(step_id="discover", data_schema=schema(self._found), errors=errors,
                                        description_placeholders=why)
        return self.async_create_entry(title=self._title, data=self._discovered)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            household, errors = await self._check(entry.data[CONF_URL], user_input[CONF_TOKEN])
            if household is not None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]})
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
                                    errors=errors)


class DiscoverFlow(OptionsFlow):
    """Configure: the appliances Home Assistant knows that the home does not have yet, to tick and add."""

    _found: list[Found]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_discover()

    async def async_step_discover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        coordinator = self.config_entry.runtime_data
        if user_input is None:
            self._found = known(self.hass, list(coordinator.data.appliances.values()) if coordinator.data else [])
            if not self._found:
                return self.async_abort(reason="nothing_found")
            return self.async_show_form(step_id="discover", data_schema=schema(self._found))
        added, errors, why = await add_ticked(coordinator.client, self._found, user_input.get(CONF_DEVICES, []))
        if added:
            await coordinator.async_reread_home()
        if errors:
            self._found = [f for f in self._found if f.device_id not in added]
            return self.async_show_form(step_id="discover", data_schema=schema(self._found), errors=errors,
                                        description_placeholders=why)
        return self.async_create_entry(data=dict(self.config_entry.options))
