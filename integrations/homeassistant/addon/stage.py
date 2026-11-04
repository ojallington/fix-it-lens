"""Write the add-on's build folder: OUT/fixit_lens (this add-on's files plus the app's tracked source in src/) and
OUT/repository.yaml, so OUT is an add-on repository and OUT/fixit_lens what the Supervisor builds.

    uv run python integrations/homeassistant/addon/stage.py OUT
    uv run python integrations/homeassistant/addon/stage.py --repo OUT    # also the integration: one HA repository
    uv run python integrations/homeassistant/addon/stage.py --repo --github OWNER/REPO --codeowner @HANDLE OUT

With --repo, OUT is the whole Home Assistant repository: an add-on repository (repository.yaml, fixit_lens/) and a
HACS integration repository (custom_components/fixit_lens/, hacs.json) at one root, with the blueprint, a README and
the LICENSE, and the Validate workflow (hacs/action and hassfest). --github names the public repository it will be:
the manifest's documentation, issue_tracker and codeowners, the add-on's url and its prebuilt GHCR image, the Builder
workflows that publish that image, and the README's My Home Assistant buttons. Without it none of these is written, and
the Supervisor builds the add-on on the device.

Only files git tracks go in, from the folders the image needs, so no key, manual, recording or private data can: an
untracked file is never copied, whatever its name. OUT must be empty or not exist.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
# What deploy/Dockerfile copies, the same set. The phone's downloaded vision files (phone/public/cv, mp) are not
# tracked, so they never go in.
SOURCE = ["agent", "app", "calls", "cards", "config", "contracts", "deploy", "fixtures", "ingest", "integrations",
          "local", "phone", "rules", "schema", "pyproject.toml", "uv.lock", ".python-version", "README.md"]


# This folder stays out of src/: the store reads every config.yaml in a repository as an app, and the copy under src/
# has no src/ of its own, so the Supervisor would build that one with an empty context.
ADDON_DIR = "integrations/homeassistant/addon/"


def tracked(root: Path = ROOT) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", *SOURCE], check=True, capture_output=True)
    return sorted(p for p in out.stdout.decode().split("\0")
                  if p and not p.startswith(ADDON_DIR) and (root / p).is_file())


# under integrations/homeassistant, tracked files only
REPO_PARTS = ["custom_components/fixit_lens", "blueprints", "dashboards"]
WORKFLOWS = HERE / "workflows"
GITHUB = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
HANDLE = re.compile(r"^@[A-Za-z0-9-]{1,39}$")
BADGES = "<!-- badges -->"
BLUEPRINT = " <!-- blueprint -->"  # after the blueprint's line: its import button, once the address is known
BLUEPRINT_PATH = "blueprints/automation/fixit_lens/job_due_notify.yaml"


def stage(out: Path, root: Path = ROOT, *, repo: bool = False, github: str | None = None,
          codeowner: str | None = None) -> Path:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    if github is not None and not (repo and GITHUB.match(github) and codeowner and HANDLE.match(codeowner)):
        raise SystemExit("--github OWNER/REPO goes with --repo and --codeowner @HANDLE")
    addon = out / "fixit_lens"
    shutil.copytree(HERE / "fixit_lens", addon)
    shutil.copy2(HERE / "repository.yaml", out / "repository.yaml")
    for rel in tracked(root):
        dst = addon / "src" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dst)
    if repo:
        ha = root / "integrations/homeassistant"
        parts = subprocess.run(["git", "-C", str(ha), "ls-files", "-z", "--", *REPO_PARTS], check=True,
                               capture_output=True).stdout.decode().split("\0")
        for rel in sorted(p for p in parts if p and (ha / p).is_file()):
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ha / rel, out / rel)
        shutil.copy2(ha / "hacs.json", out / "hacs.json")
        shutil.copy2(HERE / "REPO-README.md", out / "README.md")
        shutil.copy2(root / "LICENSE", out / "LICENSE")
        (out / ".github/workflows").mkdir(parents=True)
        shutil.copy2(WORKFLOWS / "validate.yaml", out / ".github/workflows/validate.yaml")
        if github is not None and codeowner is not None:
            publish(out, github, codeowner)
        readme = out / "README.md"
        text = readme.read_text().replace(BADGES + "\n\n", badges(github) if github else "")
        readme.write_text(text.replace(BLUEPRINT, blueprint_button(github) if github else ""))
    return addon


def publish(out: Path, github: str, codeowner: str) -> None:
    """What only the public repository knows: its address, its owner, and the add-on's image under that owner."""
    url = f"https://github.com/{github}"
    manifest = out / "custom_components/fixit_lens/manifest.json"
    m = json.loads(manifest.read_text())
    m |= {"documentation": url, "issue_tracker": f"{url}/issues", "codeowners": [codeowner]}
    manifest.write_text(json.dumps(m, indent=2) + "\n")
    config = out / "fixit_lens/config.yaml"
    image = f"ghcr.io/{github.split('/')[0].lower()}/fixit-lens-app"
    config.write_text(config.read_text() + f'url: "{url}"\nimage: "{image}"\n')
    (out / "repository.yaml").write_text((out / "repository.yaml").read_text() + f'url: "{url}"\n')
    for name in ("builder.yaml", "build-app.yaml"):
        shutil.copy2(WORKFLOWS / name, out / ".github/workflows" / name)


def badges(github: str) -> str:
    owner, name = github.split("/")
    store = "https%3A%2F%2Fgithub.com%2F" + f"{owner}%2F{name}"
    my = "https://my.home-assistant.io"
    return (f"[![Add the add-on repository to Home Assistant]({my}/badges/supervisor_store.svg)]"
            f"({my}/redirect/supervisor_store/?repository_url={store})\n"
            f"[![Open this repository in HACS]({my}/badges/hacs_repository.svg)]"
            f"({my}/redirect/hacs_repository/?owner={owner}&repository={name}&category=integration)\n"
            f"[![Set up Fix-It Lens]({my}/badges/config_flow_start.svg)]"
            f"({my}/redirect/config_flow_start/?domain=fixit_lens)\n\n")


def blueprint_button(github: str) -> str:
    """My Home Assistant's import button for the blueprint, as it is published on GitHub."""
    from urllib.parse import quote

    url = quote(f"https://github.com/{github}/blob/main/{BLUEPRINT_PATH}", safe="")
    my = "https://my.home-assistant.io"
    return (f"\n  [![Import the blueprint]({my}/badges/blueprint_import.svg)]"
            f"({my}/redirect/blueprint_import/?blueprint_url={url})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--repo", action="store_true", help="also the integration, the blueprint and hacs.json")
    ap.add_argument("--github", metavar="OWNER/REPO", help="with --repo: the public repository it will be")
    ap.add_argument("--codeowner", metavar="@HANDLE", help="with --github: the integration's code owner")
    a = ap.parse_args(argv)
    addon = stage(a.out, repo=a.repo, github=a.github, codeowner=a.codeowner)
    print(f"{addon}: {len(tracked())} files under src/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
