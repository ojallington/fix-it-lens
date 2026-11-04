-- Fix-It Lens store: SQLite migrations (contracts v1.1). One database per deployment; every row is per household.
-- Apply in order; each migration bumps PRAGMA user_version. Times are ISO 8601 UTC text; dates are YYYY-MM-DD.

-- migration 1
PRAGMA foreign_keys = ON;

CREATE TABLE households (
  id           TEXT PRIMARY KEY,
  invite_code  TEXT NOT NULL UNIQUE,
  country      TEXT NOT NULL DEFAULT 'nl' CHECK (length(country) = 2),
  lang         TEXT NOT NULL DEFAULT 'nl' CHECK (length(lang) = 2),
  cap_usd_day  REAL NOT NULL DEFAULT 0.45 CHECK (cap_usd_day >= 0),
  is_own       INTEGER NOT NULL DEFAULT 0 CHECK (is_own IN (0, 1)),   -- our own two homes, reported apart
  consent_at   TEXT,
  created_at   TEXT NOT NULL,
  deleted_at   TEXT
);

CREATE TABLE appliances (
  id            TEXT PRIMARY KEY,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  alias         TEXT NOT NULL,                       -- e.g. 'dishwasher'; ha_state(alias)
  kind          TEXT NOT NULL,                       -- card.schema appliance_kind
  brand         TEXT NOT NULL,
  model         TEXT NOT NULL,                       -- public model code; never the serial
  added_by      TEXT NOT NULL CHECK (added_by IN ('plate', 'typed')),
  plate_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (plate_confirmed IN (0, 1)),
  ha_entities   TEXT,                                -- JSON array of entity ids, or NULL
  created_at    TEXT NOT NULL,
  UNIQUE (household_id, alias)
);

CREATE TABLE cards_ref (
  appliance_id  TEXT PRIMARY KEY REFERENCES appliances(id) ON DELETE CASCADE,
  card_path     TEXT NOT NULL,                       -- cards/<brand>/<model>.yaml, or private draft path
  card_sha256   TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('checked', 'draft', 'pages_only', 'failed')),
  checked_at    TEXT
);

CREATE TABLE job_state (
  appliance_id  TEXT NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
  job_id        TEXT NOT NULL,                       -- card job id
  last_done     TEXT,                                -- date, NULL for never/unknown
  last_done_source TEXT NOT NULL CHECK (last_done_source IN ('user', 'checked', 'never', 'unknown')),
  due_on        TEXT,                                -- computed by app/schedule from the card's interval
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (appliance_id, job_id)
);

CREATE TABLE history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  appliance_id  TEXT REFERENCES appliances(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN ('voice_note', 'job_done', 'user_entry', 'fault', 'stop')),
  job_id        TEXT,
  session_id    TEXT,
  text          TEXT,
  on_date       TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE TABLE sessions (
  id            TEXT PRIMARY KEY,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  appliance_id  TEXT REFERENCES appliances(id) ON DELETE SET NULL,
  job_id        TEXT,
  mode          TEXT NOT NULL CHECK (mode IN ('api', 'local', 'capture', 'replay')),
  started_at    TEXT NOT NULL,
  ended_at      TEXT,
  outcome       TEXT CHECK (outcome IN ('done', 'stopped', 'abandoned', 'failed')),
  usd           REAL NOT NULL DEFAULT 0
);

CREATE TABLE pilot_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  appliance_id  TEXT REFERENCES appliances(id) ON DELETE CASCADE,
  job_id        TEXT,
  session_id    TEXT,
  kind          TEXT NOT NULL CHECK (kind IN ('reminder_sent', 'reminder_opened', 'job_started', 'job_done_checked', 'job_dismissed')),
  channel       TEXT CHECK (channel IN ('push', 'ics', 'caldav', 'ha', 'app')),
  at            TEXT NOT NULL
);

CREATE INDEX history_by_appliance ON history (appliance_id, on_date);
CREATE INDEX pilot_by_household ON pilot_events (household_id, kind, at);

PRAGMA user_version = 1;

-- migration 2 (push reminders, one reminder per due date)
CREATE TABLE push_subscriptions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  endpoint      TEXT NOT NULL UNIQUE,
  p256dh        TEXT NOT NULL,
  auth          TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

ALTER TABLE pilot_events ADD COLUMN reminder_key TEXT;   -- <appliance_id>:<job_id>:<due_on>; reminder_opened joins its send
CREATE INDEX pilot_by_reminder ON pilot_events (reminder_key);

PRAGMA user_version = 2;

-- migration 3 (snooze a reminder; SQLite cannot alter a CHECK, so pilot_events is rebuilt)
BEGIN;
CREATE TABLE pilot_events_v3 (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  appliance_id  TEXT REFERENCES appliances(id) ON DELETE CASCADE,
  job_id        TEXT,
  session_id    TEXT,
  kind          TEXT NOT NULL CHECK (kind IN ('reminder_sent', 'reminder_opened', 'reminder_snoozed', 'job_started',
                                              'job_done_checked', 'job_dismissed')),
  channel       TEXT CHECK (channel IN ('push', 'ics', 'caldav', 'ha', 'app')),
  at            TEXT NOT NULL,
  reminder_key  TEXT,
  snooze_until  TEXT CHECK (snooze_until IS NULL OR kind = 'reminder_snoozed')  -- the push loop sends once more then
);
INSERT INTO pilot_events_v3 (id, household_id, appliance_id, job_id, session_id, kind, channel, at, reminder_key)
  SELECT id, household_id, appliance_id, job_id, session_id, kind, channel, at, reminder_key FROM pilot_events;
DROP TABLE pilot_events;
ALTER TABLE pilot_events_v3 RENAME TO pilot_events;
CREATE INDEX pilot_by_household ON pilot_events (household_id, kind, at);
CREATE INDEX pilot_by_reminder ON pilot_events (reminder_key);
PRAGMA user_version = 3;
COMMIT;

-- migration 4 (test households: a test join on the live store is never an own home, and is left out of pilot counts,
-- due dates and push)
ALTER TABLE households ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0
  CHECK (is_test IN (0, 1) AND NOT (is_test = 1 AND is_own = 1));
PRAGMA user_version = 4;

-- migration 5 (Everywhere: the household's own schedule beside the manual's, a change log with who made each change,
-- people in a home with roles and their own alerts, and tokens for agents, Home Assistant and calendars. The card is
-- never changed: the manual's interval stays on it, cited. No model writes a date or an interval.)
BEGIN;
CREATE TABLE people (
  id            TEXT PRIMARY KEY,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  name          TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),   -- what they are called in this home
  role          TEXT NOT NULL CHECK (role IN ('admin', 'member')),   -- the two roles the big home apps use
  view_only     INTEGER NOT NULL DEFAULT 0 CHECK (view_only IN (0, 1) AND NOT (view_only = 1 AND role = 'admin')),
  created_at    TEXT NOT NULL,
  removed_at    TEXT                                 -- code keeps at least one admin who is not removed
);
CREATE UNIQUE INDEX people_name ON people (household_id, name) WHERE removed_at IS NULL;
-- Whoever opened the household is its first admin; the household token from POST /households speaks for them.
INSERT INTO people (id, household_id, name, role, created_at)
  SELECT 'p-' || lower(hex(randomblob(8))), id, 'Admin', 'admin', created_at FROM households WHERE deleted_at IS NULL;

CREATE TABLE household_tokens (
  id            TEXT PRIMARY KEY,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  by_kind       TEXT NOT NULL CHECK (by_kind IN ('app', 'agent', 'ha', 'feed')),
  scope         TEXT NOT NULL CHECK (scope IN ('read', 'write')),
  person_id     TEXT REFERENCES people(id) ON DELETE CASCADE,   -- an app token is one person's phone
  label         TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 60),   -- 'Claude desktop', 'Home Assistant'
  token_sha256  TEXT NOT NULL UNIQUE,                -- the token is shown once and never stored
  created_by    TEXT REFERENCES people(id) ON DELETE SET NULL,
  created_at    TEXT NOT NULL,
  last_used_at  TEXT,
  revoked_at    TEXT,
  CHECK ((by_kind = 'app') = (person_id IS NOT NULL)),
  CHECK (by_kind <> 'feed' OR scope = 'read')
);

CREATE TABLE people_invites (                       -- an invite into an existing home; the pilot's invite codes
  code_sha256   TEXT PRIMARY KEY,                    -- (households.invite_code) open new households and stay apart
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  role          TEXT NOT NULL CHECK (role IN ('admin', 'member')),
  view_only     INTEGER NOT NULL DEFAULT 0 CHECK (view_only IN (0, 1) AND NOT (view_only = 1 AND role = 'admin')),
  email         TEXT,                                -- whom it is for, when the admin typed an address
  created_by    TEXT REFERENCES people(id) ON DELETE SET NULL,
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  used_at       TEXT,
  used_by       TEXT REFERENCES people(id) ON DELETE SET NULL,
  CHECK (used_by IS NULL OR used_at IS NOT NULL)
);

CREATE TABLE job_schedule (                         -- one row per job the household has touched; none = the manual
  appliance_id  TEXT NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
  job_id        TEXT NOT NULL,
  kind          TEXT CHECK (kind IN ('every', 'window')),   -- NULL: follow the manual's interval
  every         INTEGER CHECK (every IS NULL OR every >= 1),
  min           INTEGER CHECK (min IS NULL OR min >= 1),
  max           INTEGER,
  unit          TEXT CHECK (unit IN ('day', 'week', 'month', 'year')),   -- never 'cycle': yours is a time
  snoozed_until TEXT,                                -- date; due_on reads it until the job is done
  assignee      TEXT REFERENCES people(id) ON DELETE SET NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (appliance_id, job_id),
  CHECK ((kind IS NULL AND every IS NULL AND min IS NULL AND max IS NULL AND unit IS NULL)
      OR (kind = 'every' AND every IS NOT NULL AND min IS NULL AND max IS NULL AND unit IS NOT NULL)
      OR (kind = 'window' AND every IS NULL AND min IS NOT NULL AND max > min AND unit IS NOT NULL))
);

CREATE TABLE job_change (                           -- every change to a job's state, with who made it; undo reads it
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  appliance_id  TEXT NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
  job_id        TEXT NOT NULL,
  at            TEXT NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('done', 'last_done', 'schedule', 'snooze', 'assignee', 'undo')),
  by_kind       TEXT NOT NULL CHECK (by_kind IN ('app', 'agent', 'ha')),   -- from the token, never from the body
  by_person     TEXT REFERENCES people(id) ON DELETE SET NULL,
  by_token      TEXT REFERENCES household_tokens(id) ON DELETE SET NULL,   -- NULL: the household token
  by_label      TEXT NOT NULL,                       -- the person's name or the token's label, as it was then
  before_json   TEXT NOT NULL,                       -- JobState (api.openapi.yaml) before and after
  after_json    TEXT NOT NULL,
  undoes        INTEGER REFERENCES job_change(id),   -- set on an 'undo'
  undone_by     INTEGER REFERENCES job_change(id),   -- Change.undone; an undo is never undone
  CHECK ((kind = 'undo') = (undoes IS NOT NULL)),
  CHECK (kind <> 'undo' OR undone_by IS NULL)
);
CREATE INDEX job_change_by_job ON job_change (appliance_id, job_id, id);

CREATE TABLE person_alerts (                        -- no row: the defaults (push and calendar, every item, no quiet).
                                                    -- An assigned job alerts only its assignee; an unassigned one,
                                                    -- everyone who follows its item.
  person_id     TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
  push          INTEGER NOT NULL DEFAULT 1 CHECK (push IN (0, 1)),
  calendar      INTEGER NOT NULL DEFAULT 1 CHECK (calendar IN (0, 1)),   -- their own feed carries what they follow
  hub           INTEGER NOT NULL DEFAULT 1 CHECK (hub IN (0, 1)),        -- HA events and hub entities name them
  items         TEXT NOT NULL DEFAULT 'all' CHECK (items IN ('all', 'chosen')),
  quiet_from    TEXT CHECK (quiet_from IS NULL OR quiet_from GLOB '[0-2][0-9]:[0-5][0-9]'),   -- household time
  quiet_to      TEXT CHECK (quiet_to IS NULL OR quiet_to GLOB '[0-2][0-9]:[0-5][0-9]'),
  CHECK ((quiet_from IS NULL) = (quiet_to IS NULL))
);
CREATE TABLE person_follows (                       -- the items of items = 'chosen'
  person_id     TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  appliance_id  TEXT NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
  PRIMARY KEY (person_id, appliance_id)
);

ALTER TABLE push_subscriptions ADD COLUMN person_id TEXT REFERENCES people(id) ON DELETE CASCADE;   -- NULL: the first admin
PRAGMA user_version = 5;
COMMIT;

-- migration 6 (the judges' link, the return link, and a household's optional name. A judges' link is multi-use: each use opens its own household,
-- marked with the link and capped by it. It is not a test home (is_test stays 0): it gets reminders, and pilot
-- counts leave it out by invite_link_id. A household opened through a link or the open door has no pilot invite, so
-- the server fills households.invite_code with a random code that is never shown. Only hashes of codes are stored.)
BEGIN;
CREATE TABLE invite_links (
  id                TEXT PRIMARY KEY,
  code_sha256       TEXT NOT NULL UNIQUE,
  kind              TEXT NOT NULL CHECK (kind IN ('judges')),
  label             TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
  cap_usd_day_home  REAL NOT NULL CHECK (cap_usd_day_home >= 0),    -- per household opened from it, per day
  cap_usd_total     REAL NOT NULL CHECK (cap_usd_total >= 0),       -- all its households together, ever
  max_homes         INTEGER NOT NULL CHECK (max_homes >= 1),        -- households it may open, ever
  expires_at        TEXT NOT NULL,
  revoked_at        TEXT,
  created_at        TEXT NOT NULL
);

ALTER TABLE households ADD COLUMN invite_link_id TEXT REFERENCES invite_links(id);
ALTER TABLE households ADD COLUMN joined_via TEXT NOT NULL DEFAULT 'invite'
  CHECK (joined_via IN ('invite', 'door', 'link') AND (joined_via = 'link') = (invite_link_id IS NOT NULL));
ALTER TABLE households ADD COLUMN name TEXT CHECK (name IS NULL OR length(name) BETWEEN 1 AND 40);   -- NULL: the usual wording
CREATE INDEX households_by_link ON households (invite_link_id) WHERE invite_link_id IS NOT NULL;

CREATE TABLE return_codes (
  code_sha256   TEXT PRIMARY KEY,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  used_at       TEXT
);
CREATE INDEX return_codes_by_household ON return_codes (household_id);
PRAGMA user_version = 6;
COMMIT;

-- migration 7 (whether the household rents: a landlord's job says "ask your landlord". NULL until someone says.)
BEGIN;
ALTER TABLE households ADD COLUMN renter INTEGER CHECK (renter IS NULL OR renter IN (0, 1));
PRAGMA user_version = 7;
COMMIT;

-- migration 8 (the card library, reports, and "not my appliance". One library row per card file: a different manual
-- file for the same model is a new file and a new row, never a silent swap. A card is shared with other homes only
-- when its manual was fetched from the maker's site and the manual's own model list covers the model, or a reviewer
-- linked it by hand. A report never edits a card: a reviewer closes it (python -m app.library), and a fix lands as a
-- new card version. Notes stay with the home that wrote them and the reviewers; other homes see counts only.)
BEGIN;
CREATE TABLE card_library (
  card_path            TEXT PRIMARY KEY,                -- cards/<brand>/..., or a private path; one file, one version
  brand                TEXT NOT NULL,
  model                TEXT NOT NULL,
  card_sha256          TEXT NOT NULL,
  origin               TEXT NOT NULL CHECK (origin IN ('maker_site', 'household_upload')),   -- card.manual.source
  covers               TEXT NOT NULL CHECK (covers IN ('exact', 'pattern', 'none')),        -- how manual.models covers model
  shared               INTEGER NOT NULL CHECK (shared IN (0, 1)),
  reason               TEXT CHECK (reason IS NULL OR reason IN ('uploaded', 'model_not_in_list', 'no_model_list',
                                                                'maker_terms', 'version_conflict',
                                                                'codes_unconfirmed', 'steps_unaccepted')),
  built_by             TEXT REFERENCES households(id) ON DELETE SET NULL,   -- never shown to another home
  built_at             TEXT NOT NULL,
  linked_by_review_by  TEXT,                            -- a reviewer's name
  linked_by_review_at  TEXT,
  CHECK ((shared = 1) = (reason IS NULL)),
  CHECK (shared = 0 OR (origin = 'maker_site' AND (covers IN ('exact', 'pattern') OR linked_by_review_by IS NOT NULL))),
  CHECK (origin = 'maker_site' OR reason = 'uploaded'),
  CHECK ((linked_by_review_by IS NULL) = (linked_by_review_at IS NULL)),
  CHECK (linked_by_review_by IS NULL OR covers = 'none')
);
CREATE INDEX card_library_by_model ON card_library (brand, model);

CREATE TABLE card_reports (
  id            TEXT PRIMARY KEY,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  appliance_id  TEXT REFERENCES appliances(id) ON DELETE SET NULL,
  card_path     TEXT NOT NULL REFERENCES card_library(card_path),
  card_sha256   TEXT NOT NULL,                          -- the version reported
  kind          TEXT NOT NULL CHECK (kind IN ('wrong_step', 'wrong_page', 'wrong_appliance', 'unsafe', 'other')),
  job_id        TEXT,
  step_id       TEXT,
  note          TEXT CHECK (note IS NULL OR length(note) BETWEEN 1 AND 500),   -- the person's words; no G0
  page          INTEGER CHECK (page IS NULL OR page >= 1),                     -- a suggested correction names it
  by_person     TEXT,                                   -- people.id; NULL for the household token
  by_label      TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  resolution    TEXT,
  created_at    TEXT NOT NULL,
  closed_at     TEXT,
  CHECK ((status = 'open') = (closed_at IS NULL)),
  CHECK ((status = 'open') = (resolution IS NULL)),
  CHECK (kind = 'wrong_appliance' OR note IS NOT NULL),
  CHECK (kind != 'wrong_step' OR step_id IS NOT NULL),
  CHECK (kind != 'wrong_appliance' OR (job_id IS NULL AND step_id IS NULL))
);
CREATE INDEX card_reports_by_card ON card_reports (card_path, status);
CREATE INDEX card_reports_by_household ON card_reports (household_id, created_at);

CREATE TABLE card_unlinks (                         -- "not my appliance": this card file is never linked to it again
  appliance_id  TEXT NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
  card_path     TEXT NOT NULL REFERENCES card_library(card_path),
  report_id     TEXT NOT NULL REFERENCES card_reports(id),
  unlinked_at   TEXT NOT NULL,
  PRIMARY KEY (appliance_id, card_path)
);
PRAGMA user_version = 8;
COMMIT;

-- migration 9 (whose phone a return link is for, and the open door: an operator opens or closes it with `invites door open|close --by <name>`, the only
-- writer; no route writes it. The latest row wins and no row means closed, so POST /households without an invite
-- code is 403 door_closed unless the latest row is open.)
BEGIN;
CREATE TABLE door_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  state  TEXT NOT NULL CHECK (state IN ('open', 'closed')),
  by     TEXT NOT NULL CHECK (length(by) BETWEEN 1 AND 60),   -- the operator's name as they typed it
  at     TEXT NOT NULL
);
-- A return link brings back one person's phone: redeeming it issues an app token (scope write, label 'phone') for
-- that person, never the home's first admin. NULL only on rows made before migration 9. The person is in the code's
-- household; the server checks that, as it does for household_tokens.
ALTER TABLE return_codes ADD COLUMN person_id TEXT REFERENCES people(id) ON DELETE CASCADE;
PRAGMA user_version = 9;
COMMIT;

-- migration 10 (who got a reminder: one row each time a person's phones are pushed one. Only the assignee or the
-- followers get it, and someone in quiet hours waits for their end, so "sent" is per person; pilot_events keeps one
-- reminder_sent row per household, written when the first person is pushed, so pilot counts do not change. After a
-- snooze a person is pushed once more if they have no row after it.)
BEGIN;
CREATE TABLE person_pushes (
  person_id     TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  reminder_key  TEXT NOT NULL,
  at            TEXT NOT NULL,
  PRIMARY KEY (person_id, reminder_key, at)
);
PRAGMA user_version = 10;
COMMIT;

-- migration 11 (the pilot cohort, one rule for the server and for eval: consented, not deleted, not our own homes or
-- a test, and joined by a pilot invite, so neither the open door nor a judges' link. Only these homes answer the
-- feedback form, and only these count in the pilot numbers.)
BEGIN;
CREATE VIEW pilot_cohort AS
  SELECT id FROM households
  WHERE consent_at IS NOT NULL AND deleted_at IS NULL AND is_own = 0 AND is_test = 0
    AND invite_link_id IS NULL AND joined_via = 'invite';
PRAGMA user_version = 11;
COMMIT;

-- migration 12 (an appliance taken out of a home: DELETE /appliances/{id} sets deleted_at and the home's views leave
-- it out. Its history, sessions and reports stay, and the card library is not touched. The old name gets the id after
-- it, so the home can give a new appliance that name.)
BEGIN;
ALTER TABLE appliances ADD COLUMN deleted_at TEXT;
PRAGMA user_version = 12;
COMMIT;

-- migration 13 (a household's bearer token can be rotated alone: it is signed with the household's bearer_epoch
-- after its id, so `invites rotate` makes the old token fail everywhere and leaves every other token as it was)
BEGIN;
ALTER TABLE households ADD COLUMN bearer_epoch INTEGER NOT NULL DEFAULT 0;
PRAGMA user_version = 13;
COMMIT;

-- migration 14 (a session the person ended with End is 'ended'; 'abandoned' is left for one no phone came back to.
-- SQLite cannot change a CHECK in place, so the table is copied. Nothing references sessions.)
BEGIN;
CREATE TABLE sessions_new (
  id            TEXT PRIMARY KEY,
  household_id  TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  appliance_id  TEXT REFERENCES appliances(id) ON DELETE SET NULL,
  job_id        TEXT,
  mode          TEXT NOT NULL CHECK (mode IN ('api', 'local', 'capture', 'replay')),
  started_at    TEXT NOT NULL,
  ended_at      TEXT,
  outcome       TEXT CHECK (outcome IN ('done', 'stopped', 'ended', 'abandoned', 'failed')),
  usd           REAL NOT NULL DEFAULT 0
);
INSERT INTO sessions_new (id, household_id, appliance_id, job_id, mode, started_at, ended_at, outcome, usd)
  SELECT id, household_id, appliance_id, job_id, mode, started_at, ended_at, outcome, usd FROM sessions;
DROP TABLE sessions;
ALTER TABLE sessions_new RENAME TO sessions;
PRAGMA user_version = 14;
COMMIT;
