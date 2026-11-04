"""Load config/models.toml: the pinned model per role and its call settings."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from calls.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "models.toml"
REFUSED_SUFFIX = "-" + "contributor"  # Contributor tier (trained on): never sent
SUMMARIES = ("auto", "concise", "detailed")
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
MIN_OUTPUT_TOKENS = 4000
MIN_OUTPUT_TOKENS_MEDIUM = 8000
ROLES = ("guide", "judge", "locate", "plate", "appliance_file", "transcribe", "sam", "sam_video")
PROVIDERS = ("baseten", "fireworks", "modal")
PARTNER_ROLES = ("guide", "judge", "locate", "plate")  # the Spark roles: Spark Open can take only these


@dataclass(frozen=True)
class RoleConfig:
    name: str
    model: str
    timeout_s: float
    effort: str | None = None
    max_output_tokens: int | None = None
    include_reasoning: bool = False
    prompt_cache_key: str | None = None
    mask_encoding: str | None = None
    reasoning_summary: str | None = None  # auto | concise | detailed: asks for reasoning.summary


@dataclass(frozen=True)
class PartnerConfig:
    """One partner serving Muse Spark Open over OpenAI chat completions, on its free credit."""

    name: str
    base_url: str
    model: str
    key_path: Path
    enabled: bool = False
    # None sends each role's own effort (the judge's "minimal", the guide's "low"), as Meta's Spark gets it, so the
    # eval compares like with like; a partner that refuses one sets its own here.
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class Config:
    roles: dict[str, RoleConfig]
    base_url: str
    key_path: Path
    max_px: int = 1024
    jpeg_quality: int = 85
    extra: dict[str, Any] = field(default_factory=dict)
    local_roles: dict[str, RoleConfig] = field(default_factory=dict)
    local_base_url: str = ""
    local_sam_url: str = ""
    # SAM 3.1 and Glimmer do not fit on the 5090 together (measured on the GPU host), so a local client sends
    # nothing to local SAM unless asked: FIXIT_LOCAL_SAM=1, for eval or a configuration without Glimmer.
    local_sam_on: bool = False
    prompts_dir: Path | None = None  # set by local(): local/prompts, the Glimmer adaptations
    partners: dict[str, PartnerConfig] = field(default_factory=dict)
    partner_order: tuple[str, ...] = ()
    partner_roles: tuple[str, ...] = ()
    routing: bool = False  # public sessions' partner roles go to a partner pool; off until the maintainer's go
    # Who may fall back to local mode when the API fails (calls/breaker.py): the operator's own homes, or every home
    # when this instance is self-hosted on its owner's computer. Never a public session.
    local_homes: tuple[str, ...] = ()
    self_hosted: bool = False

    def role(self, name: str) -> RoleConfig:
        try:
            return self.roles[name]
        except KeyError:
            raise ConfigError(f"unknown role {name!r}; roles are {sorted(self.roles)}") from None

    def local(self) -> Config:
        """The same config with each role's local row in place of its api row (FIXIT_MODE=local)."""
        if not self.local_base_url:
            raise ConfigError("config has no [backend.local] base_url")
        return replace(self, roles={**self.roles, **self.local_roles}, prompts_dir=ROOT / "local" / "prompts")


    def partner(self, name: str) -> Config:
        """The same config with each partner role's model set to that partner's Spark Open string."""
        p = self.partners.get(name)
        if p is None:
            raise ConfigError(f"no [partner.{name}] in config")
        return replace(self, roles={**self.roles, **{r: replace(self.roles[r], model=p.model)
                                                       for r in self.partner_roles}})


def _partners(data: dict[str, Any]) -> tuple[dict[str, PartnerConfig], tuple[str, ...], tuple[str, ...]]:
    raw = dict(data)
    order = tuple(str(x) for x in raw.pop("order", ()))
    roles = tuple(str(x) for x in raw.pop("roles", ()))
    if "local_overflow" in raw:
        raise ConfigError("[partner] local_overflow is gone: a public session never falls back to local mode")
    raw.pop("routing", None)
    bad = set(roles) - set(PARTNER_ROLES)
    if bad:
        raise ConfigError(f"partner roles {sorted(bad)}: only {PARTNER_ROLES} can run on Spark Open")
    unknown = (set(raw) | set(order)) - set(PROVIDERS)
    if unknown:
        raise ConfigError(f"partners {sorted(unknown)} unknown; partners are {PROVIDERS}")
    partners = {}
    for name, p in raw.items():
        model = str(p.get("model", ""))
        check_model_id(model)
        effort = p.get("reasoning_effort")
        if effort is not None and effort not in EFFORTS:
            raise ConfigError(f"partner {name}: reasoning_effort {effort!r}; use one of {EFFORTS}")
        partners[name] = PartnerConfig(name=name, base_url=str(p["base_url"]).rstrip("/"), model=model,
                                       key_path=Path(str(p["key_path"])).expanduser(),
                                       enabled=bool(p.get("enabled", False)), reasoning_effort=effort)
    return partners, order, roles


def output_floor(effort: str | None) -> int:
    if effort is not None and EFFORTS.index(effort) >= EFFORTS.index("medium"):
        return MIN_OUTPUT_TOKENS_MEDIUM
    return MIN_OUTPUT_TOKENS


def check_model_id(model: str) -> None:
    if not model or model.strip() != model:
        raise ConfigError(f"bad model ID {model!r}")
    if model.lower().endswith(REFUSED_SUFFIX):
        raise ConfigError(f"refused model ID {model!r}: Contributor tier IDs are never sent")


def _roles(rows: dict[str, Any]) -> dict[str, RoleConfig]:
    roles: dict[str, RoleConfig] = {}
    for name, raw in rows.items():
        model = str(raw.get("model", ""))
        check_model_id(model)
        effort = raw.get("effort")
        if effort is not None and effort not in EFFORTS:
            raise ConfigError(f"role {name}: effort {effort!r} not in {EFFORTS}")
        summary = raw.get("reasoning_summary")
        if summary is not None and summary not in SUMMARIES:
            raise ConfigError(f"role {name}: reasoning_summary {summary!r} not in {SUMMARIES}")
        mot = raw.get("max_output_tokens")
        if mot is not None and int(mot) < output_floor(effort):
            raise ConfigError(f"role {name}: max_output_tokens {mot} is under the floor {output_floor(effort)}")
        roles[name] = RoleConfig(
            name=name,
            model=model,
            timeout_s=float(raw["timeout_s"]),
            effort=effort,
            max_output_tokens=int(mot) if mot is not None else None,
            include_reasoning=bool(raw.get("include_reasoning", False)),
            prompt_cache_key=raw.get("prompt_cache_key"),
            mask_encoding=raw.get("mask_encoding"),
            reasoning_summary=summary,
        )
    return roles


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    data = tomllib.loads(path.read_text())
    api_rows: dict[str, Any] = data.get("roles", {})
    roles = _roles(api_rows)
    local_rows = data.get("local", {}).get("roles", {})
    unknown = set(local_rows) - set(api_rows)
    if unknown:
        raise ConfigError(f"local roles {sorted(unknown)} have no api row")
    local_roles = _roles({k: {**api_rows[k], **v} for k, v in local_rows.items()})
    missing = set(ROLES) - set(roles)
    if missing:
        raise ConfigError(f"config is missing roles {sorted(missing)}")
    meta = data.get("backend", {}).get("meta", {})
    local = data.get("backend", {}).get("local", {})
    images = data.get("images", {})
    partner = data.get("partner", {})
    partners, order, partner_roles = _partners(partner)
    fallback = data.get("local_fallback", {})
    return Config(
        roles=roles,
        base_url=str(meta.get("base_url", "")),
        key_path=Path(str(meta.get("key_path", ""))).expanduser(),
        max_px=int(images.get("max_px", 1024)),
        jpeg_quality=int(images.get("jpeg_quality", 85)),
        local_roles=local_roles,
        local_base_url=os.environ.get("FIXIT_LOCAL_URL") or str(local.get("base_url", "")),
        local_sam_url=os.environ.get("FIXIT_LOCAL_SAM_URL") or str(local.get("sam_url", "")),
        local_sam_on=os.environ.get("FIXIT_LOCAL_SAM") == "1",
        partners=partners,
        partner_order=order,
        partner_roles=partner_roles,
        routing=bool(partner.get("routing", False)),
        local_homes=tuple(str(h) for h in fallback.get("homes", [])),
        self_hosted=bool(fallback.get("self_hosted", False)),
    )
