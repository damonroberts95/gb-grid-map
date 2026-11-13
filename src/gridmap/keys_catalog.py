"""Every API key the app can use, in plain English. Data only: the keys page (server/keys.py + web/src/keys.ts)
and docs/KEYS.md are generated from / kept in sync with this (`python -m gridmap.keys_catalog --md`, and a test
checks docs/KEYS.md is current). tests/test_keys.py also asserts the catalog covers every `auth_env` in the
source registry, so a new gated source cannot ship without a guide entry.

Add a provider: append to PROVIDERS. `env` names are the variable names in the .env file (never values here).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    text: str
    url: str | None = None      # opens in a new tab
    link: str | None = None     # link label


@dataclass(frozen=True)
class KeyField:
    env: str                    # variable name in .env
    label: str                  # what to call it in the form
    hint: str = ""              # shown under the field


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    unlocks: str                # plain English: what you get
    signup_url: str
    steps: tuple[Step, ...]
    keys: tuple[KeyField, ...]
    test: dict                  # {"kind": ods|ckan|nerda|ocm, "base_url": ..., "call": human text}
    success: str                # what "Working" looks like
    gotchas: tuple[str, ...] = ()
    optional: bool = False      # the map works without it (a layer is missing)


def _ods(pid: str, name: str, host: str, short: str, unlocks: str, extra_gotchas: tuple[str, ...] = (),
         optional: bool = False) -> Provider:
    base = f"https://{host}.opendatasoft.com"
    return Provider(
        id=pid, name=name, unlocks=unlocks, signup_url=f"{base}/signup/", optional=optional,
        steps=(
            Step(f"Create a free {short} account.", f"{base}/signup/", "Create account"),
            Step("Confirm your email. Sign in.", f"{base}/login/", "Sign in"),
            Step("Open My account, then API keys."),
            Step("Click New API key. Tick browse and export."),
            Step("Paste the key below."),
        ),
        keys=(KeyField(f"{pid.upper()}_API_KEY", "API key", ""),),
        test={"kind": "ods", "base_url": base,
              "call": f"GET {base}/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header"},
        success="Key accepted. A locked dataset returns rows.",
        gotchas=("Without a key, most datasets come back empty.", *extra_gotchas))


PROVIDERS: tuple[Provider, ...] = (
    _ods("ukpn", "UK Power Networks (UKPN)", "ukpowernetworks", "UKPN",
         "Headroom, queue, faults, LV data."),
    _ods("npg", "Northern Powergrid (NPG)", "northernpowergrid", "NPG",
         "Headroom, queue, LV data."),
    _ods("enw", "Electricity North West (ENW)", "electricitynorthwest", "ENW",
         "Capacity and queue.",
         ("ENW accounts are separate from SPEN.",)),
    _ods("spen", "SP Energy Networks (SPEN)", "spenergynetworks", "SPEN",
         "Capacity, faults, queue.",
         ("Some datasets need a request-access click. Test shows how many it could read.",)),
    Provider(
        id="nged", name="National Grid Electricity Distribution (NGED)",
        unlocks="Capacity, generation, LV data.",
        signup_url="https://connecteddata.nationalgrid.co.uk/user/register",
        steps=(
            Step("Register for a free NGED account.", "https://connecteddata.nationalgrid.co.uk/user/register", "Register"),
            Step("Confirm your email. Sign in.", "https://connecteddata.nationalgrid.co.uk/user/login", "Sign in"),
            Step("Open your profile, then API Tokens."),
            Step("Create a token. Paste it below."),
        ),
        keys=(KeyField("NGED_API_KEY", "API token", "Starts with eyJ. Copy all of it."),),
        test={"kind": "ckan", "base_url": "https://connecteddata.nationalgrid.co.uk",
              "call": "GET /api/3/action/dashboard_activity_list?limit=1 with the token in the Authorization header"},
        success="HTTP 200. Without a valid token it says 403.",
        gotchas=("Use the API Tokens tab. The old 'API key' field fails.",
                 "Tokens expire. Make a new one if Test fails.")),
    Provider(
        id="nie", name="NIE Networks (Northern Ireland)",
        unlocks="Northern Ireland substations.",
        signup_url="https://nienetworks.opendatasoft.com/signup/", optional=True,
        steps=(
            Step("Create a free NIE account.", "https://nienetworks.opendatasoft.com/signup/", "Create account"),
            Step("Confirm your email. Sign in."),
            Step("Open My account, then API keys."),
            Step("Create a key. Paste it below."),
            Step("Open a dataset. Click request access if shown.", "https://nienetworks.opendatasoft.com/explore/", "Datasets"),
        ),
        keys=(KeyField("NIE_API_KEY", "API key", ""),),
        test={"kind": "ods", "base_url": "https://nienetworks.opendatasoft.com",
              "call": "GET /api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header"},
        success="Key accepted. A locked dataset returns rows.",
        gotchas=("Access is per dataset. Test says 'key valid but no access yet' until NIE approves.",)),
    _ods("ssent", "SSEN Transmission", "ssentransmission", "SSEN Transmission",
         "Scottish transmission faults.",
         optional=True),
    Provider(
        id="nerda", name="SSEN NeRDA (live substation monitoring)",
        unlocks="Live readings, about 3,800 substations.",
        signup_url="https://nerda.ssen.co.uk/",
        optional=True,
        steps=(
            Step("Open NeRDA. Log in with your SSEN account.", "https://nerda.ssen.co.uk/", "NeRDA"),
            Step("Accept the terms. Internal use only."),
            Step("Open the menu, top right."),
            Step("Make a long-term API key. Copy it. It shows once."),
            Step("Paste it below. Add your login email."),
        ),
        keys=(KeyField("NERDA_USERNAME", "Username (login email)", ""),
              KeyField("NERDA_API_KEY", "Long-term API key", "Lasts 90 days.")),
        test={"kind": "nerda", "base_url": "https://nerda-prod-apis-v2.azurewebsites.net/api/",
              "call": "GET ApiNerdaAfter for a dummy measurement with the username and key in a JSON body"},
        success="HTTP 200. A wrong key or username gives 401.",
        gotchas=("Needs both boxes.",
                 "Expires after 90 days. A new key kills the old one.",
                 "Your SSEN data portal login may work.")),
    Provider(
        id="ocm", name="Open Charge Map",
        unlocks="EV charger locations.",
        signup_url="https://openchargemap.org/site/develop/api", optional=True,
        steps=(
            Step("Register on Open Charge Map.", "https://openchargemap.org/site/develop/api", "Developer page"),
            Step("Register an application named gridmap."),
            Step("Paste the key below."),
        ),
        keys=(KeyField("OCM_API_KEY", "API key", ""),),
        test={"kind": "ocm", "base_url": "https://api.openchargemap.io/v3/poi",
              "call": "GET /v3/poi?maxresults=1 with the key in the X-API-Key header"},
        success="HTTP 200 with a charger record. A wrong key gives 401 or 403.",
        gotchas=("Keep to fair-use limits. The layer is fetched about weekly.",)),
)

ALL_ENVS = [k.env for p in PROVIDERS for k in p.keys]
BY_ENV = {k.env: p for p in PROVIDERS for k in p.keys}


def to_json() -> list[dict]:
    """The guide content as sent to the web page (no secrets: only names and help text)."""
    out = []
    for p in PROVIDERS:
        out.append({"id": p.id, "name": p.name, "unlocks": p.unlocks, "signup_url": p.signup_url, "optional": p.optional,
                    "steps": [{"text": s.text, "url": s.url, "link": s.link} for s in p.steps],
                    "keys": [{"env": k.env, "label": k.label, "hint": k.hint} for k in p.keys],
                    "success": p.success, "gotchas": list(p.gotchas), "test": p.test["call"]})
    return out


def markdown() -> str:
    L = ["# API keys guide", "",
         ("Generated from `src/gridmap/keys_catalog.py` by `uv run python -m gridmap.keys_catalog --md > docs/KEYS.md`; "
          "a test fails when this file is stale. Do not edit by hand."), "",
         ("Keys live in `.env`. Set them on the **Keys** tab in the app. Without keys, locked datasets are empty."), "",
         "| Provider | Variable(s) | Needed? |", "|---|---|---|"]
    for p in PROVIDERS:
        L.append(f"| {p.name} | {', '.join(f'`{k.env}`' for k in p.keys)} | {'optional' if p.optional else 'recommended'} |")
    for p in PROVIDERS:
        L += ["", f"## {p.name}", "", f"{p.unlocks}", "", "**Steps**", ""]
        for i, s in enumerate(p.steps, 1):
            L.append(f"{i}. {s.text}" + (f" ({s.link or 'link'}: {s.url})" if s.url else ""))
        L += ["", f"**Variables:** {', '.join(f'`{k.env}`' for k in p.keys)}", "",
              f"**Test call:** {p.test['call']}. Success: {p.success}"]
        if p.gotchas:
            L += ["", "**Notes**", ""] + [f"- {g}" for g in p.gotchas]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    import sys
    sys.stdout.write(markdown() if "--md" in sys.argv else "use --md\n")
