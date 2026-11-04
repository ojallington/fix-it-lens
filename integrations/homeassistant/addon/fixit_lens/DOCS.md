# Fix-It Lens

Fix-It Lens reads your appliances' own manuals into upkeep jobs, reminds you when each is due, and guides you through
the job with your phone's camera, checking each step before the next. This add-on runs the whole app inside Home
Assistant. Its panel in the sidebar makes invites for your phone and connects each home to Home Assistant.

## What it opens

- **The panel** in the sidebar, through Home Assistant's own login. Nothing else can reach it. Home Assistant adds
  it to the sidebar once you switch on **Show in sidebar** on this add-on's page; until then **Open web UI** opens it.
- **Port 8790**, the app for your phone. It is off until you give it a host port under **Network**.
- Home Assistant reaches the app inside its own network. The add-on calls no Home Assistant service and reads no
  device.

Your data (the store, its secret, push's key) is in the add-on's own folder, and it is in Home Assistant's backups.
Uninstalling the add-on deletes it.

## Set up your phone

The phone's camera and its reminders need an HTTPS address the phone can reach.

1. Home Assistant needs a certificate in its `ssl` folder, for example from the Let's Encrypt or DuckDNS add-on.
2. In this add-on's **Configuration**: set **HTTPS** on, check the certificate and key file names, and set **Phone
   address** to your Home Assistant's name with port 8790, for example `https://homeassistant.example.org:8790`.
3. Under **Network**, give 8790 a host port (8790), save and restart the add-on.
4. Open the panel, press **Make an invite**, and scan the QR code with your phone's camera. Join, and add the app to
   your home screen.

Each invite lets one home join, once. Make another for each new home; the people in a home invite each other from the
app (About, People and alerts).

## Connect to Home Assistant

Install the Fix-It Lens integration first. Copy the `custom_components/fixit_lens` folder (from
`integrations/homeassistant/` in the repository) into your Home Assistant config folder's `custom_components`, then
restart Home Assistant.

Then press **Connect to Home Assistant** next to the home in the panel. Home Assistant shows Fix-It Lens under
Settings, Devices & services, Discovered: press **Add**. You get a to-do list and a calendar of the due jobs, a device
per appliance with a sensor per job, its next job and its overdue count, the `fixit_lens_job_due` event, and the
`fixit_lens.mark_done` and `fixit_lens.snooze` actions.

The key it makes can see that home's upkeep jobs and mark them done or snoozed; it never controls a device. Pressing
**Connect again** makes a new key and hands it to the same entry. The earlier key stays until you remove it in the
app, under About, Connections.

## Replay and live

**Replay** (the default) plays the recorded sessions: no key, no model call, nothing spent.

**Live** calls Meta's models with your own key, billed to you, inside each home's daily cap. Put the key in a file
named `meta-model-api.key` in this add-on's config folder (`/addon_configs/<id>_fixit_lens/` in the Samba or SSH
add-on), with nothing else in the file, set **Calls** to `live`, and restart. The add-on reads the key from that file;
it never copies it, and it does not start in live mode without it.
