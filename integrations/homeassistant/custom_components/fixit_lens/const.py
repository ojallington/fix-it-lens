"""Constants for the Fix-It Lens integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "fixit_lens"
DEFAULT_TITLE = "Fix-It Lens"
CONF_URL = "url"
CONF_TOKEN = "token"
SCAN_INTERVAL = timedelta(minutes=1)  # GET /due: a job done in the app shows here within a minute
HOME_INTERVAL = timedelta(minutes=30)  # GET /household and /appliances: names, makers and models change rarely
EVENT_JOB_DUE = "fixit_lens_job_due"
SERVICE_MARK_DONE = "mark_done"
SERVICE_SNOOZE = "snooze"
