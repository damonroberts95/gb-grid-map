# API keys guide

Generated from `src/gridmap/keys_catalog.py` by `uv run python -m gridmap.keys_catalog --md > docs/KEYS.md`; a test fails when this file is stale. Do not edit by hand.

Keys live in the `.env` file (never committed, never printed). The easiest way to set them is the **Keys** tab in the app, which writes `.env` for you and can test each key. The map works without any key, but locked datasets are then empty.

| Provider | Variable(s) | Needed? |
|---|---|---|
| UK Power Networks (UKPN) | `UKPN_API_KEY` | recommended |
| Northern Powergrid (NPG) | `NPG_API_KEY` | recommended |
| Electricity North West (ENW) | `ENW_API_KEY` | recommended |
| SP Energy Networks (SPEN) | `SPEN_API_KEY` | recommended |
| National Grid Electricity Distribution (NGED) | `NGED_API_KEY` | recommended |
| NIE Networks (Northern Ireland) | `NIE_API_KEY` | optional |
| SSEN Transmission | `SSENT_API_KEY` | optional |
| SSEN NeRDA (live substation monitoring) | `NERDA_USERNAME`, `NERDA_API_KEY` | optional |
| Open Charge Map | `OCM_API_KEY` | optional |

## UK Power Networks (UKPN)

**What it unlocks:** South-east England, London and East of England: substation capacity heatmap, connection queue, fault levels, smart-meter and low-voltage data. About 97 of 136 datasets are locked without a key.

**Steps**

1. Open the UK Power Networks data portal and create a free account (email and password). (Create account: https://ukpowernetworks.opendatasoft.com/signup/)
2. Confirm your email address using the link they send you, then sign in. (Sign in: https://ukpowernetworks.opendatasoft.com/login/)
3. Click your name at the top right, choose My account, then the API keys tab.
4. Click New API key, give it any name (for example gridmap), and tick the permissions to browse and export datasets.
5. Copy the long key that is shown and paste it into the box below.

**Variables:** `UKPN_API_KEY`

**Test call:** GET https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: The portal accepts the key and at least one locked dataset returns rows.

**Gotchas**

- Without a key the portal answers 'OK' but sends empty files for most datasets, so the map would just be missing data.
- Anonymous use is also limited to a few thousand requests a day per IP.

## Northern Powergrid (NPG)

**What it unlocks:** North-east England and Yorkshire: capacity heatmap, long-term network statements, queue and secondary-substation data. Over half of their datasets are locked without a key.

**Steps**

1. Open the Northern Powergrid data portal and create a free account (email and password). (Create account: https://northernpowergrid.opendatasoft.com/signup/)
2. Confirm your email address using the link they send you, then sign in. (Sign in: https://northernpowergrid.opendatasoft.com/login/)
3. Click your name at the top right, choose My account, then the API keys tab.
4. Click New API key, give it any name (for example gridmap), and tick the permissions to browse and export datasets.
5. Copy the long key that is shown and paste it into the box below.

**Variables:** `NPG_API_KEY`

**Test call:** GET https://northernpowergrid.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: The portal accepts the key and at least one locked dataset returns rows.

**Gotchas**

- Without a key the portal answers 'OK' but sends empty files for most datasets, so the map would just be missing data.
- Anonymous use is also limited to a few thousand requests a day per IP.

## Electricity North West (ENW)

**What it unlocks:** North-west England: almost all datasets (about 141 of 148), including network capacity and the connection queue.

**Steps**

1. Open the Electricity North West data portal and create a free account (email and password). (Create account: https://electricitynorthwest.opendatasoft.com/signup/)
2. Confirm your email address using the link they send you, then sign in. (Sign in: https://electricitynorthwest.opendatasoft.com/login/)
3. Click your name at the top right, choose My account, then the API keys tab.
4. Click New API key, give it any name (for example gridmap), and tick the permissions to browse and export datasets.
5. Copy the long key that is shown and paste it into the box below.

**Variables:** `ENW_API_KEY`

**Test call:** GET https://electricitynorthwest.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: The portal accepts the key and at least one locked dataset returns rows.

**Gotchas**

- Without a key the portal answers 'OK' but sends empty files for most datasets, so the map would just be missing data.
- Anonymous use is also limited to a few thousand requests a day per IP.
- ENW is now part of SP Energy Networks; the portal and its accounts still work separately from the SPEN one.

## SP Energy Networks (SPEN)

**What it unlocks:** Central Scotland, Merseyside and North Wales: network capacity, fault levels and queue data (148 of 171 datasets are locked).

**Steps**

1. Open the SP Energy Networks data portal and create a free account (email and password). (Create account: https://spenergynetworks.opendatasoft.com/signup/)
2. Confirm your email address using the link they send you, then sign in. (Sign in: https://spenergynetworks.opendatasoft.com/login/)
3. Click your name at the top right, choose My account, then the API keys tab.
4. Click New API key, give it any name (for example gridmap), and tick the permissions to browse and export datasets.
5. Copy the long key that is shown and paste it into the box below.

**Variables:** `SPEN_API_KEY`

**Test call:** GET https://spenergynetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: The portal accepts the key and at least one locked dataset returns rows.

**Gotchas**

- Without a key the portal answers 'OK' but sends empty files for most datasets, so the map would just be missing data.
- Anonymous use is also limited to a few thousand requests a day per IP.
- Some SPEN datasets need a separate access request even with a valid key: on the dataset page click the request-access button and wait for approval. The Test button reports how many locked datasets it could read.

## National Grid Electricity Distribution (NGED)

**What it unlocks:** Midlands, South West and Wales: network capacity map, generation register, substation lists and LV data. Registered-only resources are hidden (the links read 'redacted') without a token.

**Steps**

1. Register for a free account on the NGED Connected Data portal. (Register: https://connecteddata.nationalgrid.co.uk/user/register)
2. Confirm your email and sign in. (Sign in: https://connecteddata.nationalgrid.co.uk/user/login)
3. Click your username at the top right to open your profile, then open the API Tokens tab.
4. Create a token (any name), copy it and paste it into the box below. It is a very long string starting with 'eyJ' (a JWT).

**Variables:** `NGED_API_KEY`

**Test call:** GET /api/3/action/dashboard_activity_list?limit=1 with the token in the Authorization header. Success: HTTP 200 (the portal only answers this to a signed-in token; without one it says 403).

**Gotchas**

- Use the token from the API Tokens tab, not the older 'API key' field on your profile page: that one is not accepted.
- Tokens can expire; if Test starts failing, create a new one.

## NIE Networks (Northern Ireland)

**What it unlocks:** Northern Ireland: grid and primary substations, secondary transformers and network faults. All 20 datasets are locked without a key. Optional: the rest of the map does not need it.

**Steps**

1. Create a free account on the NIE Networks open data portal. (Create account: https://nienetworks.opendatasoft.com/signup/)
2. Confirm your email, sign in, then click your name at the top right, My account, API keys tab.
3. Create a new API key and paste it into the box below.
4. Open a dataset you want, for example the grid and primary sites one, and if you see a request-access button, click it. (Browse datasets: https://nienetworks.opendatasoft.com/explore/)

**Variables:** `NIE_API_KEY`

**Test call:** GET /api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: The portal accepts the key and at least one locked dataset returns rows.

**Gotchas**

- Access is granted per dataset: a valid key can still get 'forbidden' on a dataset until NIE approves your request. Test says 'key valid but no access yet' in that case.

## SSEN Transmission

**What it unlocks:** Scotland's high-voltage transmission network: fault levels and other planning datasets. Optional: most of the map does not need it.

**Steps**

1. Open the SSEN Transmission data portal and create a free account (email and password). (Create account: https://ssentransmission.opendatasoft.com/signup/)
2. Confirm your email address using the link they send you, then sign in. (Sign in: https://ssentransmission.opendatasoft.com/login/)
3. Click your name at the top right, choose My account, then the API keys tab.
4. Click New API key, give it any name (for example gridmap), and tick the permissions to browse and export datasets.
5. Copy the long key that is shown and paste it into the box below.

**Variables:** `SSENT_API_KEY`

**Test call:** GET https://ssentransmission.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: The portal accepts the key and at least one locked dataset returns rows.

**Gotchas**

- Without a key the portal answers 'OK' but sends empty files for most datasets, so the map would just be missing data.
- Anonymous use is also limited to a few thousand requests a day per IP.

## SSEN NeRDA (live substation monitoring)

**What it unlocks:** Live readings from about 3,800 street substations in SSEN's southern network. Optional.

**Steps**

1. Open the NeRDA portal and click Login. Sign up or sign in with your SSEN account. (NeRDA portal: https://nerda.ssen.co.uk/)
2. Accept the terms. They allow internal use only.
3. Open the menu. It is the three-line button at the top right.
4. Choose the option to generate a long-term API key. It lasts 90 days. Copy it straight away: it is shown once.
5. Paste the key below, and also type the email address you log in to NeRDA with in the username box.

**Variables:** `NERDA_USERNAME`, `NERDA_API_KEY`

**Test call:** GET ApiNerdaAfter for a dummy measurement with the username and key in a JSON body. Success: HTTP 200 (a wrong key or username gives 401 'API key invalid').

**Gotchas**

- Both boxes are needed: the username is your login email.
- The key expires after 90 days. Generating a new one in the portal makes the old one stop working.
- Your SSEN data portal login may already work here.

## Open Charge Map

**What it unlocks:** Public electric-vehicle charger locations, used for the EV demand layer. Optional: it is only used when you rebuild that layer by hand.

**Steps**

1. Sign in or register on Open Charge Map, then open the API developer page. (Developer page: https://openchargemap.org/site/develop/api)
2. Follow the 'API key' section there to register an application (name it gridmap).
3. Copy the key it gives you and paste it into the box below.

**Variables:** `OCM_API_KEY`

**Test call:** GET /v3/poi?maxresults=1 with the key in the X-API-Key header. Success: HTTP 200 with a charger record (a wrong key gives 401/403).

**Gotchas**

- Please keep to their fair-use limits; the layer is fetched about weekly, not continuously.
