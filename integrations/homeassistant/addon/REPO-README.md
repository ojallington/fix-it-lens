# Fix-It Lens for Home Assistant

<!-- badges -->

Fix-It Lens reads your appliances' own manuals into upkeep jobs, reminds you when each is due, and guides you through
the job with your phone's camera. This repository is two things for Home Assistant:

- **The add-on** (`fixit_lens/`, an app in Home Assistant 2026.2 and later): the whole app inside Home Assistant, with
  a page in the sidebar. In Home Assistant: Settings, Apps, Install app, the menu, Repositories, add this repository's
  address, then install Fix-It Lens. Its Documentation tab explains the rest.
- **The integration** (`custom_components/fixit_lens/`): your homes' upkeep jobs as a to-do list, a calendar, and a
  device per appliance with a sensor per job, its next job and its overdue count; a `fixit_lens_job_due` event; and
  `fixit_lens.mark_done` and `fixit_lens.snooze` actions. In HACS, add this repository as a custom repository of type
  Integration and download it, or copy the folder into your config's `custom_components`. Restart Home Assistant. With the add-on, its sidebar page connects each home for you.
- **A blueprint** (`blueprints/automation/fixit_lens/job_due_notify.yaml`): a notification on your phone when a job
  falls due, naming the job and its appliance, with Done, In N days and Open (the job in the app) buttons. One that
  falls due in your quiet hours (22:00 to 08:00 unless you change them) is sent when they end. <!-- blueprint -->
- **A ready dashboard** (`dashboards/fixit_lens.yaml`), from Home Assistant's own cards only: the to-do list, the
  calendar, and per appliance its next job, when it is due, a red line when a job is overdue (amber when one is due),
  and a link to the job in the app. The integration's Download diagnostics gives the same dashboard for your own
  appliances (its `dashboard`): paste it into a new dashboard's raw configuration editor.

![The ready dashboard: the to-do list, the week's calendar, and the dishwasher with one overdue job](dashboards/screenshot.png)

The integration controls no device and calls no Home Assistant service. The add-on reads no device either.

This repository is written from the Fix-It Lens repository (`integrations/homeassistant/`) by
`integrations/homeassistant/addon/stage.py --repo`; change things there, not here. Apache-2.0 (`LICENSE`).
