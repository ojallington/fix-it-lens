# The Home Assistant add-on

Fix-It Lens inside Home Assistant: the app, and a panel in the sidebar that makes invites and connects homes
(`fixit_lens/DOCS.md` is the add-on's Documentation tab; the start is `deploy/ha_addon.py`).

The Supervisor builds an add-on from its own folder, so the app's source has to be in it. `stage.py` writes that
folder: this add-on's files, plus every tracked file the image needs under `src/` (no key, no manual, no private
data, nothing untracked).

    uv run python integrations/homeassistant/addon/stage.py OUT     # OUT/fixit_lens, OUT/repository.yaml
    docker build -t fixit-lens-addon OUT/fixit_lens                 # the same image the Supervisor builds

To try it in your own Home Assistant, copy `OUT/fixit_lens` into the `addons` folder (Samba or SSH add-on). Then go to
Settings, Add-ons, Add-on store, and use **Check for updates** in the menu: it appears under Local add-ons. `OUT`, as
written, is also an add-on repository (`repository.yaml` at its root).

`check.py` runs the image the way the Supervisor would: a stub Supervisor, the options file, the ingress address.

## Publishing the Home Assistant repository

`stage.py --repo --github OWNER/REPO --codeowner @HANDLE OUT` writes the repository as it will be published: the
add-on with a prebuilt image (`ghcr.io/<owner>/fixit-lens-app`), the integration with its documentation, issue tracker
and code owner filled in, the Validate workflow (hacs/action and hassfest) and the Builder workflows, and the README's
My Home Assistant buttons. Then, on GitHub:

1. Push `OUT` as the repository's `main`. Settings: a description, issues on, topics `home-assistant`, `hacs`,
   `home-assistant-integration` and `home-assistant-addon`. GitHub reads `LICENSE` as Apache-2.0.
2. Let Builder publish the image, then make the `fixit-lens-app` package public (packages start private).
3. Once Validate passes, make a full GitHub release (not just a tag) named for the manifest's `version`, `0.1.0`.
4. For HACS's default list, open the pull request to `hacs/default` from a personal account (not an organization).
   It adds `OWNER/REPO` to the `integration` list, with links to the release and to the passing Validate run.
5. Once Home Assistant 2026.11 is released (its hassfest image is what Validate runs), add the integration's
   category to the manifest, right after `"name"` (hassfest wants the other keys in order):

       "categories": ["productivity_information.calendars_todos"],

   The 2026.10 hassfest refuses the key; the 2026.11 code accepts this value (checked against the `dev` branch's
   hassfest). Raise `version` to `0.1.1`, let Validate pass, and release it as in step 3. Until then the
   integration is listed without a category.

Add-ons have no catalog: people add the repository's address, or use the README's button.
