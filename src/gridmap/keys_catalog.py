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
            Step(f"Open the {short} data portal and create a free account (email and password).", f"{base}/signup/", "Create account"),
            Step("Confirm your email address using the link they send you, then sign in.", f"{base}/login/", "Sign in"),
            Step("Click your name at the top right, choose My account, then the API keys tab."),
            Step("Click New API key, give it any name (for example gridmap), and tick the permissions to browse and export datasets."),
            Step("Copy the long key that is shown and paste it into the box below."),
        ),
        keys=(KeyField(f"{pid.upper()}_API_KEY", "API key", "A long string of letters and numbers."),),
        test={"kind": "ods", "base_url": base,
              "call": f"GET {base}/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header"},
        success="The portal accepts the key and at least one locked dataset returns rows.",
        gotchas=("Without a key the portal answers 'OK' but sends empty files for most datasets, so the map would just be missing data.",
                 "Anonymous use is also limited to a few thousand requests a day per IP.", *extra_gotchas))


PROVIDERS: tuple[Provider, ...] = (
    _ods("ukpn", "UK Power Networks (UKPN)", "ukpowernetworks", "UK Power Networks",
         "South-east England, London and East of England: substation capacity heatmap, connection queue, fault levels, smart-meter and low-voltage data. About 97 of 136 datasets are locked without a key."),
    _ods("npg", "Northern Powergrid (NPG)", "northernpowergrid", "Northern Powergrid",
         "North-east England and Yorkshire: capacity heatmap, long-term network statements, queue and secondary-substation data. Over half of their datasets are locked without a key."),
    _ods("enw", "Electricity North West (ENW)", "electricitynorthwest", "Electricity North West",
         "North-west England: almost all datasets (about 141 of 148), including network capacity and the connection queue.",
         ("ENW is now part of SP Energy Networks; the portal and its accounts still work separately from the SPEN one.",)),
    _ods("spen", "SP Energy Networks (SPEN)", "spenergynetworks", "SP Energy Networks",
         "Central Scotland, Merseyside and North Wales: network capacity, fault levels and queue data (148 of 171 datasets are locked).",
         ("Some SPEN datasets need a separate access request even with a valid key: on the dataset page click the request-access button and wait for approval. The Test button reports how many locked datasets it could read.",)),
    Provider(
        id="nged", name="National Grid Electricity Distribution (NGED)",
        unlocks="Midlands, South West and Wales: network capacity map, generation register, substation lists and LV data. Registered-only resources are hidden (the links read 'redacted') without a token.",
        signup_url="https://connecteddata.nationalgrid.co.uk/user/register",
        steps=(
            Step("Register for a free account on the NGED Connected Data portal.", "https://connecteddata.nationalgrid.co.uk/user/register", "Register"),
            Step("Confirm your email and sign in.", "https://connecteddata.nationalgrid.co.uk/user/login", "Sign in"),
            Step("Click your username at the top right to open your profile, then open the API Tokens tab."),
            Step("Create a token (any name), copy it and paste it into the box below. It is a very long string starting with 'eyJ' (a JWT)."),
        ),
        keys=(KeyField("NGED_API_KEY", "API token", "Very long, starts with eyJ. Copy all of it."),),
        test={"kind": "ckan", "base_url": "https://connecteddata.nationalgrid.co.uk",
              "call": "GET /api/3/action/dashboard_activity_list?limit=1 with the token in the Authorization header"},
        success="HTTP 200 (the portal only answers this to a signed-in token; without one it says 403).",
        gotchas=("Use the token from the API Tokens tab, not the older 'API key' field on your profile page: that one is not accepted.",
                 "Tokens can expire; if Test starts failing, create a new one.")),
    Provider(
        id="nie", name="NIE Networks (Northern Ireland)",
        unlocks="Northern Ireland: grid and primary substations, secondary transformers and network faults. All 20 datasets are locked without a key. Optional: the rest of the map does not need it.",
        signup_url="https://nienetworks.opendatasoft.com/signup/", optional=True,
        steps=(
            Step("Create a free account on the NIE Networks open data portal.", "https://nienetworks.opendatasoft.com/signup/", "Create account"),
            Step("Confirm your email, sign in, then click your name at the top right, My account, API keys tab."),
            Step("Create a new API key and paste it into the box below."),
            Step("Open a dataset you want, for example the grid and primary sites one, and if you see a request-access button, click it.", "https://nienetworks.opendatasoft.com/explore/", "Browse datasets"),
        ),
        keys=(KeyField("NIE_API_KEY", "API key", ""),),
        test={"kind": "ods", "base_url": "https://nienetworks.opendatasoft.com",
              "call": "GET /api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header"},
        success="The portal accepts the key and at least one locked dataset returns rows.",
        gotchas=("Access is granted per dataset: a valid key can still get 'forbidden' on a dataset until NIE approves your request. Test says 'key valid but no access yet' in that case.",)),
    _ods("ssent", "SSEN Transmission", "ssentransmission", "SSEN Transmission",
         "Scotland's high-voltage transmission network: fault levels and other planning datasets. Optional: most of the map does not need it.",
         optional=True),
    Provider(
        id="nerda", name="SSEN NeRDA (live substation monitoring)",
        unlocks="Live readings from about 3,800 street substations in SSEN's southern network. Optional.",
        signup_url="https://nerda.ssen.co.uk/",
        optional=True,
        steps=(
            Step("Open the NeRDA portal and click Login. Sign up or sign in with your SSEN account.", "https://nerda.ssen.co.uk/", "NeRDA portal"),
            Step("Accept the terms. They allow internal use only."),
            Step("Open the menu. It is the three-line button at the top right."),
            Step("Choose the option to generate a long-term API key. It lasts 90 days. Copy it straight away: it is shown once."),
            Step("Paste the key below, and also type the email address you log in to NeRDA with in the username box."),
        ),
        keys=(KeyField("NERDA_USERNAME", "Username (your login email)", ""),
              KeyField("NERDA_API_KEY", "Long-term API key", "Lasts 90 days.")),
        test={"kind": "nerda", "base_url": "https://nerda-prod-apis-v2.azurewebsites.net/api/",
              "call": "GET ApiNerdaAfter for a dummy measurement with the username and key in a JSON body"},
        success="HTTP 200 (a wrong key or username gives 401 'API key invalid').",
        gotchas=("Both boxes are needed: the username is your login email.",
                 "The key expires after 90 days. Generating a new one in the portal makes the old one stop working.",
                 "Your SSEN data portal login may already work here.")),
    Provider(
        id="ocm", name="Open Charge Map",
        unlocks="Public electric-vehicle charger locations, used for the EV demand layer. Optional: it is only used when you rebuild that layer by hand.",
        signup_url="https://openchargemap.org/site/develop/api", optional=True,
        steps=(
            Step("Sign in or register on Open Charge Map, then open the API developer page.", "https://openchargemap.org/site/develop/api", "Developer page"),
            Step("Follow the 'API key' section there to register an application (name it gridmap)."),
            Step("Copy the key it gives you and paste it into the box below."),
        ),
        keys=(KeyField("OCM_API_KEY", "API key", ""),),
        test={"kind": "ocm", "base_url": "https://api.openchargemap.io/v3/poi",
              "call": "GET /v3/poi?maxresults=1 with the key in the X-API-Key header"},
        success="HTTP 200 with a charger record (a wrong key gives 401/403).",
        gotchas=("Please keep to their fair-use limits; the layer is fetched about weekly, not continuously.",)),
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
         ("Keys live in the `.env` file (never committed, never printed). The easiest way to set them is the **Keys** tab in the app, "
          "which writes `.env` for you and can test each key. The map works without any key, but locked datasets are then empty."), "",
         "| Provider | Variable(s) | Needed? |", "|---|---|---|"]
    for p in PROVIDERS:
        L.append(f"| {p.name} | {', '.join(f'`{k.env}`' for k in p.keys)} | {'optional' if p.optional else 'recommended'} |")
    for p in PROVIDERS:
        L += ["", f"## {p.name}", "", f"**What it unlocks:** {p.unlocks}", "", "**Steps**", ""]
        for i, s in enumerate(p.steps, 1):
            L.append(f"{i}. {s.text}" + (f" ({s.link or 'link'}: {s.url})" if s.url else ""))
        L += ["", f"**Variables:** {', '.join(f'`{k.env}`' for k in p.keys)}", "",
              f"**Test call:** {p.test['call']}. Success: {p.success}"]
        if p.gotchas:
            L += ["", "**Gotchas**", ""] + [f"- {g}" for g in p.gotchas]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    import sys
    sys.stdout.write(markdown() if "--md" in sys.argv else "use --md\n")
