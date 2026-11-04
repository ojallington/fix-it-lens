"""Devices: one service device per Fix-It Lens home (the to-do list and the calendar), and under it one device per
appliance, named as the home calls it, with the maker and model from its card (its sensors)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FixitCoordinator
from .core import Appliance


def home_device(coordinator: FixitCoordinator) -> DeviceInfo:
    snap = coordinator.data
    return DeviceInfo(identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
                      name=snap.home if snap and snap.home else "Fix-It Lens", manufacturer="Fix-It Lens",
                      model="Home", entry_type=DeviceEntryType.SERVICE,
                      configuration_url=coordinator.client.base_url)


def appliance_device(coordinator: FixitCoordinator, a: Appliance) -> DeviceInfo:
    entry_id = coordinator.config_entry.entry_id
    return DeviceInfo(identifiers={(DOMAIN, f"{entry_id}:{a.id}")}, name=a.name, manufacturer=a.maker,
                      model=a.model, via_device=(DOMAIN, entry_id), configuration_url=coordinator.client.base_url)


class FixitEntity(CoordinatorEntity[FixitCoordinator]):
    """An entity on the home's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FixitCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = home_device(coordinator)


class ApplianceEntity(CoordinatorEntity[FixitCoordinator]):
    """An entity on one appliance's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FixitCoordinator, appliance_id: str) -> None:
        super().__init__(coordinator)
        self.appliance_id = appliance_id
        self._attr_device_info = appliance_device(coordinator, coordinator.data.appliance(appliance_id))
