# API keys guide

Generated from `src/gridmap/keys_catalog.py` by `uv run python -m gridmap.keys_catalog --md > docs/KEYS.md`; a test fails when this file is stale. Do not edit by hand.

Keys live in `.env`. Set them on the **Keys** tab in the app. Without keys, locked datasets are empty.

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

Headroom, queue, faults, LV data.

**Steps**

1. Create a free UKPN account. (Create account: https://ukpowernetworks.opendatasoft.com/signup/)
2. Confirm your email. Sign in. (Sign in: https://ukpowernetworks.opendatasoft.com/login/)
3. Open My account, then API keys.
4. Click New API key. Tick browse and export.
5. Paste the key below.

**Variables:** `UKPN_API_KEY`

**Test call:** GET https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: Key accepted. A locked dataset returns rows.

**Notes**

- Without a key, most datasets come back empty.

## Northern Powergrid (NPG)

Headroom, queue, LV data.

**Steps**

1. Create a free NPG account. (Create account: https://northernpowergrid.opendatasoft.com/signup/)
2. Confirm your email. Sign in. (Sign in: https://northernpowergrid.opendatasoft.com/login/)
3. Open My account, then API keys.
4. Click New API key. Tick browse and export.
5. Paste the key below.

**Variables:** `NPG_API_KEY`

**Test call:** GET https://northernpowergrid.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: Key accepted. A locked dataset returns rows.

**Notes**

- Without a key, most datasets come back empty.

## Electricity North West (ENW)

Capacity and queue.

**Steps**

1. Create a free ENW account. (Create account: https://electricitynorthwest.opendatasoft.com/signup/)
2. Confirm your email. Sign in. (Sign in: https://electricitynorthwest.opendatasoft.com/login/)
3. Open My account, then API keys.
4. Click New API key. Tick browse and export.
5. Paste the key below.

**Variables:** `ENW_API_KEY`

**Test call:** GET https://electricitynorthwest.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: Key accepted. A locked dataset returns rows.

**Notes**

- Without a key, most datasets come back empty.
- ENW accounts are separate from SPEN.

## SP Energy Networks (SPEN)

Capacity, faults, queue.

**Steps**

1. Create a free SPEN account. (Create account: https://spenergynetworks.opendatasoft.com/signup/)
2. Confirm your email. Sign in. (Sign in: https://spenergynetworks.opendatasoft.com/login/)
3. Open My account, then API keys.
4. Click New API key. Tick browse and export.
5. Paste the key below.

**Variables:** `SPEN_API_KEY`

**Test call:** GET https://spenergynetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: Key accepted. A locked dataset returns rows.

**Notes**

- Without a key, most datasets come back empty.
- Some datasets need a request-access click. Test shows how many it could read.

## National Grid Electricity Distribution (NGED)

Capacity, generation, LV data.

**Steps**

1. Register for a free NGED account. (Register: https://connecteddata.nationalgrid.co.uk/user/register)
2. Confirm your email. Sign in. (Sign in: https://connecteddata.nationalgrid.co.uk/user/login)
3. Open your profile, then API Tokens.
4. Create a token. Paste it below.

**Variables:** `NGED_API_KEY`

**Test call:** GET /api/3/action/dashboard_activity_list?limit=1 with the token in the Authorization header. Success: HTTP 200. Without a valid token it says 403.

**Notes**

- Use the API Tokens tab. The old 'API key' field fails.
- Tokens expire. Make a new one if Test fails.

## NIE Networks (Northern Ireland)

Northern Ireland substations.

**Steps**

1. Create a free NIE account. (Create account: https://nienetworks.opendatasoft.com/signup/)
2. Confirm your email. Sign in.
3. Open My account, then API keys.
4. Create a key. Paste it below.
5. Open a dataset. Click request access if shown. (Datasets: https://nienetworks.opendatasoft.com/explore/)

**Variables:** `NIE_API_KEY`

**Test call:** GET /api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: Key accepted. A locked dataset returns rows.

**Notes**

- Access is per dataset. Test says 'key valid but no access yet' until NIE approves.

## SSEN Transmission

Scottish transmission faults.

**Steps**

1. Create a free SSEN Transmission account. (Create account: https://ssentransmission.opendatasoft.com/signup/)
2. Confirm your email. Sign in. (Sign in: https://ssentransmission.opendatasoft.com/login/)
3. Open My account, then API keys.
4. Click New API key. Tick browse and export.
5. Paste the key below.

**Variables:** `SSENT_API_KEY`

**Test call:** GET https://ssentransmission.opendatasoft.com/api/explore/v2.1/catalog/datasets and one locked dataset's records, with the key in the Authorization header. Success: Key accepted. A locked dataset returns rows.

**Notes**

- Without a key, most datasets come back empty.

## SSEN NeRDA (live substation monitoring)

Live readings, about 3,800 substations.

**Steps**

1. Open NeRDA. Log in with your SSEN account. (NeRDA: https://nerda.ssen.co.uk/)
2. Accept the terms. Internal use only.
3. Open the menu, top right.
4. Make a long-term API key. Copy it. It shows once.
5. Paste it below. Add your login email.

**Variables:** `NERDA_USERNAME`, `NERDA_API_KEY`

**Test call:** GET ApiNerdaAfter for a dummy measurement with the username and key in a JSON body. Success: HTTP 200. A wrong key or username gives 401.

**Notes**

- Needs both boxes.
- Expires after 90 days. A new key kills the old one.
- Your SSEN data portal login may work.

## Open Charge Map

EV charger locations.

**Steps**

1. Register on Open Charge Map. (Developer page: https://openchargemap.org/site/develop/api)
2. Register an application named gridmap.
3. Paste the key below.

**Variables:** `OCM_API_KEY`

**Test call:** GET /v3/poi?maxresults=1 with the key in the X-API-Key header. Success: HTTP 200 with a charger record. A wrong key gives 401 or 403.

**Notes**

- Keep to fair-use limits. The layer is fetched about weekly.
