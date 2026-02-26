
import os
import json
import time
import hashlib
import requests
import functools
import asyncio
from concurrent.futures import ThreadPoolExecutor
from peeringdb import resource
from peeringdb.client import Client as PeeringDBClient
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from typing import List, Dict, Any, Optional

# root_path='/audit' ensures FastAPI generates correct internal URLs
app = FastAPI(root_path="/audit")

# Setup templates directory
templates = Jinja2Templates(directory="templates")

CONFIG_FILE = "config.json"
PEERINGDB_BASE = "https://www.peeringdb.com/api"

DEFAULT_CONFIG = {
    "ASNS": [21859, 4229],
    "METRO_MAP": {
        "Ashburn": "Washington DC (IAD)",
        "Reston": "Washington DC (IAD)",
        "Vienna": "Washington DC (IAD)",
        "San Jose": "Silicon Valley (SJC)",
        "Santa Clara": "Silicon Valley (SJC)",
        "Palo Alto": "Silicon Valley (SJC)"
    }
}

# PeeringDB configuration
PEERINGDB_API_KEY = os.environ.get("PEERINGDB_API_KEY", "")
PEERINGDB_DB_PATH = os.environ.get("PEERINGDB_DB_PATH", "/app/data/peeringdb.sqlite3")

# Global PeeringDB client instance
pdb_client = None


# ---------------------------------------------------------------------------
# File-based JSON cache with configurable TTL
# ---------------------------------------------------------------------------
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".api_cache")
CACHE_TTL = 604800  # 7 days in seconds (PeeringDB data is relatively static)
RIPESTAT_CACHE_TTL = 432000  # 5 days in seconds (AS-path data changes infrequently)

def _cache_key(prefix: str, value: str) -> str:
    """Generate a filesystem-safe cache key."""
    h = hashlib.sha256(value.encode()).hexdigest()[:16]
    return f"{prefix}_{h}"

def _read_cache(key: str, ttl: int = CACHE_TTL):
    """Read a cached value if it exists and hasn't expired."""
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            entry = json.load(f)
        if time.time() - entry.get("ts", 0) > ttl:
            os.remove(path)
            return None
        return entry["data"]
    except Exception:
        return None

def _write_cache(key: str, data, ttl: int = CACHE_TTL):
    """Write a value to the file cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{key}.json")
    try:
        with open(path, "w") as f:
            json.dump({"ts": time.time(), "data": data}, f)
    except Exception as e:
        print(f"[Cache] Write error for {key}: {e}")

def clear_file_cache():
    """Remove all entries from the file cache."""
    if os.path.isdir(CACHE_DIR):
        for fname in os.listdir(CACHE_DIR):
            try:
                os.remove(os.path.join(CACHE_DIR, fname))
            except Exception:
                pass
    print("[Cache] File cache cleared")

# ---------------------------------------------------------------------------
# AS-Path Frequency Analysis helpers
# ---------------------------------------------------------------------------
RIPESTAT_BASE = "https://stat.ripe.net/data"
_BGP_HEADERS = {"User-Agent": "bgp-audit/1.0"}


def _fetch_as_path(asn: int) -> List[List[int]]:
    """
    Fetch observed AS paths that traverse *asn* by looking up its announced
    prefixes via RIPEstat and then pulling the AS-path for a sample prefix.

    Returns a list of integer AS-path lists, e.g.
        [[3356, 1299, 21859], [174, 1299, 21859], ...]

    The full path data is cached per-ASN for 5 days so repeat calls never hit the
    network.
    """
    cache_key = _cache_key("aspath", str(asn))
    cached = _read_cache(cache_key, ttl=RIPESTAT_CACHE_TTL)
    if cached is not None:
        return cached

    paths: List[List[int]] = []

    # Step 1 – get announced prefixes for this ASN
    prefixes = _fetch_prefixes_for_asn(asn)
    if not prefixes:
        print(f"[ASPath] No prefixes found for AS{asn}, skipping cache")
        return paths

    # Step 2 – pick a sample prefix (first one) and pull its looking-glass
    sample_prefix = prefixes[0]
    try:
        url = (
            f"{RIPESTAT_BASE}/looking-glass/data.json"
            f"?resource={sample_prefix}"
        )
        resp = requests.get(url, timeout=20, headers=_BGP_HEADERS)
        if resp.status_code == 200 and resp.text.strip():
            rrcs = resp.json().get("data", {}).get("rrcs", [])
            seen = set()
            for rrc in rrcs:
                for peer in rrc.get("peers", []):
                    raw = peer.get("as_path", "")
                    if not raw:
                        continue
                    try:
                        int_path = [int(a) for a in raw.split()]
                    except ValueError:
                        continue
                    path_key = tuple(int_path)
                    if path_key not in seen:
                        seen.add(path_key)
                        paths.append(int_path)
            print(f"[ASPath] AS{asn} prefix {sample_prefix}: {len(paths)} unique paths")

            # Only cache successful results
            if paths:
                _write_cache(cache_key, paths, ttl=RIPESTAT_CACHE_TTL)
        else:
            print(f"[ASPath] Bad response for {sample_prefix}: status {resp.status_code}")
    except Exception as e:
        print(f"[ASPath] Error fetching looking-glass for {sample_prefix}: {e}")

    return paths


def _fetch_prefixes_for_asn(asn: int) -> List[str]:
    """Return a list of prefixes originated by *asn* (cached for 5 days)."""
    cache_key = _cache_key("pfx", str(asn))
    cached = _read_cache(cache_key, ttl=RIPESTAT_CACHE_TTL)
    if cached is not None:
        return cached

    prefixes: List[str] = []
    try:
        url = f"{RIPESTAT_BASE}/announced-prefixes/data.json?resource=AS{asn}"
        resp = requests.get(url, timeout=15, headers=_BGP_HEADERS)
        if resp.status_code == 200 and resp.text.strip():
            for p in resp.json().get("data", {}).get("prefixes", []):
                pfx = p.get("prefix")
                if pfx:
                    prefixes.append(pfx)

            # Only cache successful results
            if prefixes:
                _write_cache(cache_key, prefixes, ttl=RIPESTAT_CACHE_TTL)
                print(f"[ASPath] Cached {len(prefixes)} prefixes for AS{asn}")
        else:
            print(f"[ASPath] Bad response for AS{asn} prefixes: status {resp.status_code}")
    except Exception as e:
        print(f"[ASPath] Error fetching prefixes for AS{asn}: {e}")

    return prefixes


def _extract_first_hop(as_path: List[int], local_asns: set) -> Optional[int]:
    """
    Walk an AS path (ordered origin → collector) and return the ASN that
    appears immediately *before* any local ASN.  BGP paths are recorded
    collector-side so the order is [collector … transit … first_hop, local].

    Example (local = {21859}):
        [3356, 1299, 21859]  → 1299   (1299 hands traffic to 21859)
        [174, 21859]         → 174
        [21859]              → None   (only local)
    """
    for i, asn in enumerate(as_path):
        if asn in local_asns and i > 0:
            candidate = as_path[i - 1]
            if candidate not in local_asns:
                return candidate
    return None


def _analyze_zenlayer_paths(
    local_asns: List[int],
) -> tuple:
    """
    Analyze AS paths for Zenlayer's own prefixes to discover:
      1. Direct peers  – the ASN immediately adjacent to a Zenlayer ASN
      2. Per-peer downstreams – ASNs further up the path that route *through*
         each direct peer to reach Zenlayer

    This requires only **one RIPEstat lookup per local ASN** (typically 2
    calls for AS21859 + AS4229), making it fast enough for a synchronous
    endpoint.

    Returns (direct_peers: set, peer_downstreams: dict)
        direct_peers      – {asn, ...}
        peer_downstreams   – {peer_asn: {downstream_asn, ...}, ...}
    """
    local_set = set(local_asns)
    direct_peers: set = set()
    peer_downstreams: Dict[int, set] = {}

    for asn in local_asns:
        paths = _fetch_as_path(asn)
        for path in paths:
            # Find the position of a Zenlayer ASN in this path
            for i, path_asn in enumerate(path):
                if path_asn in local_set and i > 0:
                    first_hop = path[i - 1]
                    if first_hop in local_set:
                        continue
                    direct_peers.add(first_hop)
                    # Everything before the first_hop transits through it
                    if first_hop not in peer_downstreams:
                        peer_downstreams[first_hop] = set()
                    for j in range(0, i - 1):
                        if path[j] not in local_set:
                            peer_downstreams[first_hop].add(path[j])
                    break  # only need the first Zenlayer occurrence per path

    print(
        f"[ASPath] Zenlayer path analysis: "
        f"{len(direct_peers)} direct peers, "
        f"{sum(len(v) for v in peer_downstreams.values())} downstream ASNs"
    )
    return direct_peers, peer_downstreams


# Global app state
zenlayer_state = {
    "networks": [],
    "facilities": [],
    "unique_cities": [],
    "unique_metros": [],
    "config": {}
}

def load_config() -> Dict[str, Any]:
    """Load configuration from file or create default if missing."""
    if not os.path.exists(CONFIG_FILE) or os.path.getsize(CONFIG_FILE) == 0:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return DEFAULT_CONFIG

def save_config(data: Dict[str, Any]):
    """Save configuration to file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def _initialize_peeringdb_sync():
    """Initialize PeeringDB local database (runs in thread)."""
    global pdb_client

    try:
        print("[PeeringDB] Initializing local database...")

        # Configure peeringdb client
        cfg = {
            "sync": {
                "url": "https://www.peeringdb.com/api",
                "strip_tz": 1,
                "timeout": 0,
            },
            "orm": {
                "backend": "django_peeringdb",
                "database": {
                    "engine": "sqlite3",
                    "name": PEERINGDB_DB_PATH,
                }
            }
        }

        # Add authentication only if API key is provided
        if PEERINGDB_API_KEY:
            print(f"[PeeringDB] Using API key for authentication")
            cfg["sync"]["user"] = PEERINGDB_API_KEY
            cfg["sync"]["password"] = ""
        else:
            print(f"[PeeringDB] No API key provided, using anonymous access")

        # Initialize the client (this sets up Django)
        pdb_client = PeeringDBClient(cfg=cfg)

        print(f"[PeeringDB] Local database initialized at {PEERINGDB_DB_PATH}")

        # Check if database tables already exist by querying Django
        tables_exist = False
        if os.path.exists(PEERINGDB_DB_PATH):
            db_size_mb = os.path.getsize(PEERINGDB_DB_PATH) / (1024 * 1024)
            print(f"[PeeringDB] Database file size: {db_size_mb:.1f} MB")

            # Check if tables exist
            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='peeringdb_organization';")
                    result = cursor.fetchone()
                    tables_exist = result is not None
                    if tables_exist:
                        print(f"[PeeringDB] Database tables already exist, skipping migrations")
            except Exception as e:
                print(f"[PeeringDB] Could not check tables: {e}")

        # Only run migrations if tables don't exist
        if not tables_exist:
            # Run Django migrations to create database schema
            from django.core.management import call_command
            print("[PeeringDB] Creating database schema...")
            call_command('migrate', verbosity=0)
            print("[PeeringDB] Database schema created")

        # Check if sync is needed (database age)
        if os.path.exists(PEERINGDB_DB_PATH):
            age_seconds = time.time() - os.path.getmtime(PEERINGDB_DB_PATH)
            age_days = age_seconds / 86400
            print(f"[PeeringDB] Database age: {age_days:.1f} days")

            # Re-check size after potential migrations
            size_mb = os.path.getsize(PEERINGDB_DB_PATH) / (1024 * 1024)
            print(f"[PeeringDB] Current database size: {size_mb:.1f} MB")

            # Auto-sync if database is older than 1.5 days or very small (just schema)
            if age_days > 1.5 or size_mb < 1:
                print("[PeeringDB] Database needs sync, syncing...")
                try:
                    pdb_client.update_all()
                    print("[PeeringDB] Sync complete")
                except Exception as sync_error:
                    print(f"[PeeringDB] Sync failed (likely rate limited): {sync_error}")
                    # If the DB is still schema-only after a failed sync, fall back to REST API
                    post_sync_size = os.path.getsize(PEERINGDB_DB_PATH) / (1024 * 1024) if os.path.exists(PEERINGDB_DB_PATH) else 0
                    if post_sync_size < 1:
                        print("[PeeringDB] Database still empty after failed sync, falling back to REST API")
                        pdb_client = None
                    else:
                        print("[PeeringDB] Will continue with existing database and retry later")
        else:
            # Initial sync on first run
            print("[PeeringDB] Performing initial sync (this may take a few minutes)...")
            try:
                pdb_client.update_all()
                print("[PeeringDB] Initial sync complete")
            except Exception as sync_error:
                print(f"[PeeringDB] Initial sync failed (likely rate limited): {sync_error}")
                print("[PeeringDB] Will continue without local database")
                pdb_client = None  # Disable local database if sync fails

    except Exception as e:
        print(f"[PeeringDB] Initialization error: {e}")
        import traceback
        traceback.print_exc()
        print("[PeeringDB] Will fall back to API calls if needed")

def fetch_peeringdb(endpoint: str, timeout: int = 10) -> List[Dict[str, Any]]:
    """
    Query PeeringDB local database using the peeringdb-py client.
    Parses the endpoint string to determine which resource to query and what filters to apply.

    Supported endpoints:
    - net?asn__in=21859,4229
    - net?id__in=1,2,3
    - netfac?net_id__in=1,2,3
    - netfac?fac_id=123
    - netfac?fac_id__in=1,2,3
    - fac?id__in=1,2,3
    - ixfac?fac_id__in=1,2,3
    - ix?id__in=1,2,3
    - netixlan?net_id__in=1,2,3
    """
    global pdb_client

    try:
        # If client not initialized, fall back to REST API
        if pdb_client is None:
            print(f"[PeeringDB] Client not initialized, falling back to REST API: {endpoint}")
            cache_key = _cache_key("pdb_rest", endpoint)
            cached = _read_cache(cache_key)
            if cached is not None:
                print(f"[PeeringDB] REST cache hit for '{endpoint}': {len(cached)} results")
                return cached
            url = f"{PEERINGDB_BASE}/{endpoint}"
            headers = {"User-Agent": "bgp-audit/1.0"}
            if PEERINGDB_API_KEY:
                headers["Authorization"] = f"Api-Key {PEERINGDB_API_KEY}"
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                print(f"[PeeringDB] REST API '{endpoint}': {len(data)} results")
                _write_cache(cache_key, data)
                return data
            except Exception as rest_err:
                print(f"[PeeringDB] REST API error for '{endpoint}': {rest_err}")
                return []

        # Parse endpoint
        parts = endpoint.strip("/").split("?")
        model_name = parts[0]
        filters = {}

        if len(parts) > 1:
            # Parse query parameters
            for param in parts[1].split("&"):
                if "=" in param:
                    key, value = param.split("=", 1)

                    # Handle __in filters - convert to list
                    if "__in" in key:
                        # Remove __in suffix for peeringdb-py filter syntax
                        key_base = key.replace("__in", "")
                        # Convert comma-separated values to list of integers
                        try:
                            filters[f"{key_base}__in"] = [int(v.strip()) for v in value.split(",")]
                        except ValueError:
                            # If not integers, keep as strings
                            filters[f"{key_base}__in"] = [v.strip() for v in value.split(",")]
                    else:
                        # Single value filter
                        try:
                            filters[key] = int(value)
                        except ValueError:
                            filters[key] = value

        # Map endpoint names to peeringdb resources
        resource_map = {
            "net": resource.Network,
            "fac": resource.Facility,
            "netfac": resource.NetworkFacility,
            "ix": resource.InternetExchange,
            "ixfac": resource.InternetExchangeFacility,
            "netixlan": resource.NetworkIXLan,
        }

        if model_name not in resource_map:
            print(f"[PeeringDB] Unsupported resource: {model_name}")
            return []

        # Query the local database using peeringdb-py client
        res_type = resource_map[model_name]

        # Use all() method and chain filter() if needed
        queryset = pdb_client.all(res_type)
        if filters:
            queryset = queryset.filter(**filters)

        # Convert to list of dicts
        output = []
        for obj in queryset:
            # Convert Django model instance to dict using model_to_dict
            # But we need to get all fields, not just form fields
            obj_dict = {}

            # Get all field values from the Django model
            for field in obj._meta.fields:
                # Use attname to get the raw column value (e.g., 'fac_id' not 'fac')
                # This handles ForeignKey fields correctly
                field_name = field.attname
                field_value = getattr(obj, field.attname)

                # Convert datetime objects to ISO format strings
                if hasattr(field_value, 'isoformat'):
                    obj_dict[field_name] = field_value.isoformat()
                else:
                    obj_dict[field_name] = field_value

            output.append(obj_dict)

        print(f"[PeeringDB] Local query '{endpoint}': {len(output)} results")
        # Debug: log field names of first record to help diagnose key errors
        if output:
            print(f"[PeeringDB] Fields in first '{model_name}' record: {list(output[0].keys())}")
        return output

    except Exception as e:
        print(f"[PeeringDB] Query error for '{endpoint}': {e}")
        import traceback
        traceback.print_exc()
        # Fall back to empty list on error
        return []

def _initialize_footprint_sync():
    """Build the Zenlayer facility/city/metro map (runs in thread)."""
    print("Zenlayer BGP Audit: Loading dynamic configuration...")
    config = load_config()
    zenlayer_state["config"] = config

    asns = config.get("ASNS", DEFAULT_CONFIG["ASNS"])
    asn_query = ",".join(map(str, asns))

    nets = fetch_peeringdb(f"net?asn__in={asn_query}")
    zenlayer_state["networks"] = nets
    net_ids = [n["id"] for n in nets]

    if not net_ids:
        print(f"Warning: No networks found for ASNs {asn_query}.")
        zenlayer_state["unique_cities"] = []
        zenlayer_state["unique_metros"] = []
        return

    netfacs = fetch_peeringdb(f"netfac?net_id__in={','.join(map(str, net_ids))}")
    # Try both field name variants (API uses 'fac_id', Django model may use 'facility_id')
    fac_key = "fac_id" if netfacs and "fac_id" in netfacs[0] else "facility_id"
    if netfacs:
        print(f"[Footprint] netfac field names: {list(netfacs[0].keys())}")
    fac_ids = list(set([nf[fac_key] for nf in netfacs if nf.get(fac_key)]))

    if fac_ids:
        facilities = fetch_peeringdb(f"fac?id__in={','.join(map(str, fac_ids))}")
        print(f"[Footprint] Loaded {len(facilities)} total facilities")
        mapping = config.get("METRO_MAP", {})

        cities = set()
        metros = set()

        for fac in facilities:
            city = fac.get("city")
            if city:
                cities.add(city)
                if city in mapping:
                    metro_name = mapping[city]
                    fac["metro"] = metro_name
                    metros.add(metro_name)

        zenlayer_state["facilities"] = sorted(facilities, key=lambda x: x["name"])
        zenlayer_state["unique_cities"] = sorted(list(cities))
        zenlayer_state["unique_metros"] = sorted(list(metros))

        # Debug: Show facility distribution per city
        from collections import Counter
        city_counts = Counter([f.get("city") for f in facilities if f.get("city")])
        print(f"[Footprint] Facilities per city: {dict(city_counts)}")

    print(f"Zenlayer BGP Audit: Footprint loaded. ASNs: {asns}, Metros: {len(zenlayer_state['unique_metros'])}, Cities: {len(zenlayer_state['unique_cities'])}")

@app.on_event("startup")
async def initialize_footprint():
    """Initialize PeeringDB and footprint in background thread."""
    # Run both PeeringDB initialization and footprint loading in thread
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _initialize_peeringdb_sync)
    await loop.run_in_executor(None, _initialize_footprint_sync)
@app.get("/", response_class=HTMLResponse)
@app.get("", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    """Serve the main dashboard UI."""
    config = zenlayer_state.get("config", {})
    return templates.TemplateResponse("index.html", {
        "request": request,
        "metros": zenlayer_state.get("unique_metros", []),
        "cities": zenlayer_state.get("unique_cities", []),
        "facilities": zenlayer_state.get("facilities", []),
        "metro_mapping": config.get("METRO_MAP", {})
    })

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Serve the settings editor UI."""
    return templates.TemplateResponse("settings.html", {"request": request})
@app.get("/api/settings")
async def get_settings():
    """Return the current configuration plus all unique cities found in footprint."""
    return {
        "config": load_config(),
        "discovered_cities": zenlayer_state["unique_cities"]
    }

@app.post("/api/settings")
async def update_settings(new_config: Dict[str, Any]):
    """Update configuration and re-initialize state."""
    save_config(new_config)
    clear_file_cache()
    _get_discovery_data.cache_clear()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _initialize_peeringdb_sync)
    await loop.run_in_executor(None, _initialize_footprint_sync)
    return {"status": "success", "message": "Settings updated."}

@app.post("/api/resync")
async def resync_footprint():
    """Trigger a full PeeringDB sync and footprint re-initialization."""
    global pdb_client
    pdb_client = None  # Force re-init of client
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _initialize_peeringdb_sync)
    await loop.run_in_executor(None, _initialize_footprint_sync)
    return {
        "status": "success",
        "cities": zenlayer_state.get("unique_cities", []),
        "metros": zenlayer_state.get("unique_metros", []),
        "facilities": len(zenlayer_state.get("facilities", [])),
    }

@app.post("/api/cache/clear")
async def clear_cache():
    """Clear all caches (file + in-memory) for debugging."""
    clear_file_cache()
    _get_discovery_data.cache_clear()
    print("[API] All caches cleared")
    return {"status": "success", "message": "Cache cleared."}

@functools.lru_cache(maxsize=1024)
def _get_discovery_data(fac_id: Optional[int], location_name: Optional[str], location_type: str, category: str):
    """Internal discovery logic with memoization for facility, city, or metro scope."""
    net_ids = []
    
    if fac_id:
        # Search specifically in one data center
        netfacs = fetch_peeringdb(f"netfac?fac_id={fac_id}")
        # Handle both API format (net_id) and local DB format (net or network_id)
        net_ids = [
            nf.get("net_id") or nf.get("net") or nf.get("network_id")
            for nf in netfacs
            if nf.get("net_id") or nf.get("net") or nf.get("network_id")
        ]
    elif location_name:
        # Find relevant facilities
        target_fac_ids = []
        if location_type == "metro":
            # Find all cities mapped to this metro
            config = load_config()
            mapping = config.get("METRO_MAP", {})
            cities_in_metro = [city for city, m in mapping.items() if m == location_name]
            target_fac_ids = [f["id"] for f in zenlayer_state["facilities"] if f.get("city") in cities_in_metro]
        else:
            # location_type == "city"
            matching_facs = [f for f in zenlayer_state["facilities"] if f.get("city") == location_name]
            target_fac_ids = [f["id"] for f in matching_facs]
            print(f"[Discovery] City '{location_name}': found {len(target_fac_ids)} facilities: {target_fac_ids}")
            if matching_facs:
                print(f"[Discovery] Facility details: {[(f['id'], f['name'], f.get('city')) for f in matching_facs]}")

        if target_fac_ids:
            fac_query = ",".join(map(str, target_fac_ids))
            netfacs = fetch_peeringdb(f"netfac?fac_id__in={fac_query}")
            # Handle both API format (net_id) and local DB format (net or network_id)
            net_ids = list(set([
                nf.get("net_id") or nf.get("net") or nf.get("network_id")
                for nf in netfacs
                if nf.get("net_id") or nf.get("net") or nf.get("network_id")
            ]))
    
    if not net_ids:
        return []

    # Batch net details retrieval
    batch_size = 50
    all_nets = []
    for i in range(0, len(net_ids), batch_size):
        chunk = net_ids[i:i + batch_size]
        nets = fetch_peeringdb(f"net?id__in={','.join(map(str, chunk))}")
        all_nets.extend(nets)

    discovered = []
    peer_types = ["Content", "Eyeball Network", "Enterprise", "Educational/Research"]
    
    for net in all_nets:
        info_type = net.get("info_type", "")
        is_nsp = info_type == "NSP"
        is_transit = "Transit" in info_type
        is_peer_cat = any(t in info_type for t in peer_types)

        match = False
        if category == "upstream" and (is_nsp or is_transit): match = True
        elif category == "peers" and is_peer_cat: match = True
        elif category == "all": match = True

        if match:
            discovered.append({
                "asn": net.get("asn"),
                "name": net.get("name"),
                "info_type": info_type,
                "policy": net.get("policy_general", "Not Specified"),
                "traffic_range": net.get("traffic_range", "Unknown")
            })
    return sorted(discovered, key=lambda x: x["name"])

@app.get("/api/discover")
async def discover_networks(
    fac_id: Optional[int] = None,
    location: Optional[str] = None,
    location_type: str = "city",
    category: str = "upstream"
):
    try:
        print(f"[API] /api/discover called: fac_id={fac_id}, location={location}, location_type={location_type}, category={category}")
        result = _get_discovery_data(fac_id, location, location_type, category)
        print(f"[API] Returning {len(result)} networks")
        return result
    except Exception as e:
        print(f"[API] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/discover/summary")
async def discover_summary(
    location: Optional[str] = None,
    location_type: str = "city",
    fac_id: Optional[int] = None
):
    """Return a summary with 3-way path-quality classification."""
    try:
        config = load_config()
        zenlayer_asns = config.get("ASNS", DEFAULT_CONFIG["ASNS"])
        local_set = set(zenlayer_asns)

        all_nets = _get_discovery_data(fac_id, location, location_type, "all")

        # AS-Path analysis: find direct peers from Zenlayer's own paths
        direct_peer_asns, _ = _analyze_zenlayer_paths(zenlayer_asns)
        facility_asns = {n["asn"] for n in all_nets if n.get("asn")}
        direct_at_facility = direct_peer_asns & facility_asns

        # ----- LOCAL IX detection: Only check IXes at this facility -----
        # Step 1: Get all IXes at the target facilities
        if fac_id:
            target_fac_ids = [fac_id]
        elif location:
            if location_type == "metro":
                config = load_config()
                mapping = config.get("METRO_MAP", {})
                cities_in_metro = [city for city, m in mapping.items() if m == location]
                matching_facs = [f for f in zenlayer_state["facilities"] if f.get("city") in cities_in_metro]
                target_fac_ids = [f["id"] for f in matching_facs]
                print(f"[Summary] Metro '{location}' includes cities: {cities_in_metro}")
                print(f"[Summary] Found {len(target_fac_ids)} facilities in metro")
            else:
                matching_facs = [f for f in zenlayer_state["facilities"] if f.get("city") == location]
                target_fac_ids = [f["id"] for f in matching_facs]
                print(f"[Summary] City '{location}': {len(target_fac_ids)} facilities found")
                if matching_facs:
                    for fac in matching_facs:
                        print(f"[Summary]   - Fac {fac['id']}: {fac['name']} ({fac.get('city')})")
        else:
            target_fac_ids = []

        local_ixes = []
        zenlayer_local_ix_ids = set()
        if target_fac_ids:
            # Get IXes at these facilities
            fac_query = ",".join(map(str, target_fac_ids))
            ixfacs = fetch_peeringdb(f"ixfac?fac_id__in={fac_query}")
            local_ix_ids = list(set([ixf["ix_id"] for ixf in ixfacs if ixf.get("ix_id")]))

            if local_ix_ids:
                # Get IX details
                ix_query = ",".join(map(str, local_ix_ids))
                local_ixes_data = fetch_peeringdb(f"ix?id__in={ix_query}")

                # Check which local IXes Zenlayer is connected to
                zenlayer_net_ids = [n["id"] for n in zenlayer_state.get("networks", [])]
                if zenlayer_net_ids:
                    net_id_query = ",".join(map(str, zenlayer_net_ids))
                    zl_ixlan = fetch_peeringdb(f"netixlan?net_id__in={net_id_query}")
                    zenlayer_all_ix_ids = set([rec["ix_id"] for rec in zl_ixlan if rec.get("ix_id")])
                    zenlayer_local_ix_ids = set(local_ix_ids) & zenlayer_all_ix_ids

                    # Build list of IXes at this facility that Zenlayer uses
                    for ix in local_ixes_data:
                        if ix["id"] in zenlayer_local_ix_ids:
                            local_ixes.append({
                                "id": ix["id"],
                                "name": ix.get("name", f"IX-{ix['id']}"),
                                "name_long": ix.get("name_long", ""),
                            })

        print(f"[Summary] {location}: {len(local_ixes)} local IXes where Zenlayer is present")

        # Simplified classification without global IX checking
        # Direct On-Net: BGP peers at the facility
        direct_neighbors = [
            {"asn": n["asn"], "name": n["name"]}
            for n in all_nets if n["asn"] in direct_at_facility
        ]

        # Transit: Everything else at the facility
        transit_at_facility = facility_asns - direct_at_facility - local_set

        return {
            "total": len(all_nets),
            "direct_on_net_count": len(direct_neighbors),
            "exchange_ixp_count": len(local_ixes),  # Number of IXes, not networks
            "transit_count": len(transit_at_facility),
            "direct_peers": sorted(direct_neighbors, key=lambda x: x["name"]),
            "direct_peer_asns": sorted(list(direct_at_facility)),
            "local_ixes": sorted(local_ixes, key=lambda x: x["name"]),
        }
    except Exception as e:
        print(f"[API] Summary error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export")
async def export_networks(
    location: Optional[str] = None,
    location_type: str = "city",
    fac_id: Optional[int] = None,
    category: str = "all"
):
    """Export current network view as CSV."""
    import io
    import csv
    
    try:
        # Get networks using same logic as discover endpoint
        networks = _get_discovery_data(fac_id, location, location_type, category)
        
        # Get summary for classification
        config = load_config()
        zenlayer_asns = config.get("ASNS", DEFAULT_CONFIG["ASNS"])
        direct_peer_asns, _ = _analyze_zenlayer_paths(zenlayer_asns)
        facility_asns = {n["asn"] for n in networks if n.get("asn")}
        direct_at_facility = direct_peer_asns & facility_asns
        
        # Build CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow([
            "ASN",
            "Network Name",
            "Type",
            "Classification",
            "Peering Policy",
            "Traffic Range"
        ])
        
        # Data rows
        for net in networks:
            asn = net.get("asn")
            classification = "Direct On-Net" if asn in direct_at_facility else "Upstream Transit"
            
            writer.writerow([
                f"AS{asn}" if asn else "",
                net.get("name", ""),
                net.get("info_type", ""),
                classification,
                net.get("policy", "Not Specified"),
                net.get("traffic_range", "Unknown")
            ])
        
        # Generate filename
        location_str = location or f"facility_{fac_id}"
        safe_name = location_str.replace(" ", "_").replace("/", "-")
        filename = f"Zenlayer_Networks_{safe_name}_{category}.csv"
        
        # Return CSV
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
        
    except Exception as e:
        print(f"[API] Export error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
