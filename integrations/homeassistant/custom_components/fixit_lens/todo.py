"""One to-do list of upkeep jobs. Ticking an item marks the job done today in Fix-It Lens (logged as Home
Assistant's, undoable in the app); nothing else is writable."""

from __future__ import annotations

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import FixitConfigEntry
from .core import AuthFailed, Refused, Unreachable
from .entity import FixitEntity


async def async_setup_entry(hass: HomeAssistant, entry: FixitConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([Upkeep(entry.runtime_data)])


class Upkeep(FixitEntity, TodoListEntity):
    _attr_translation_key = "upkeep"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM \
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM

    def __init__(self, coordinator) -> None:  # type: ignore[no-untyped-def]
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:upkeep"
        self.entity_id = "todo.fix_it_lens_upkeep"

    @property
    def todo_items(self) -> list[TodoItem]:
        today = dt_util.now().date()
        return [TodoItem(uid=j.key, summary=j.summary, due=j.due_on, description=j.description(),
                         status=TodoItemStatus.NEEDS_ACTION if j.due_on is None or j.due_on <= today
                         else TodoItemStatus.COMPLETED)
                for j in self.coordinator.data.jobs]

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status != TodoItemStatus.COMPLETED:
            return  # the server decides when a job is due again; un-ticking changes nothing
        job = next((j for j in self.coordinator.data.jobs if j.key == item.uid), None)
        if job is None:
            raise HomeAssistantError("no such job")
        try:
            await self.coordinator.client.mark_done(job, dt_util.now().date())
        except Refused as e:
            raise ServiceValidationError(str(e)) from e
        except (AuthFailed, Unreachable) as e:
            raise HomeAssistantError(f"Fix-It Lens: {e}") from e
        await self.coordinator.async_request_refresh()
