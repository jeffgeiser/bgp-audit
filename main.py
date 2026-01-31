
import os
import json
import time
import hashlib
import requests
import functools
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
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


# ---------------------------------------------------------------------------
# File-based JSON cache with 24-hour TTL
# ---------------------------------------------------------------------------
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".api_cache")
CACHE_TTL = 86400  # 24 hours in seconds

def _cache_key(prefix: str, value: str) -> str:
    """Generate a filesystem-safe cache key."""
    h = hashlib.sha256(value.encode()).hexdigest()[:16]
    return f"{prefix}_{h}"

def _read_cache(key: str):
    """Read a cached value if it exists and hasn't expired."""
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            entry = json.load(f)
        if time.time() - entry.get("ts", 0) > CACHE_TTL:
            os.remove(path)
            return None
        return entry["data"]
    except Exception:
        return None

def _write_cache(key: str, data):
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

    The full path data is cached per-ASN so repeat calls never hit the
    network.
    """
    cache_key = _cache_key("aspath", str(asn))
    cached = _read_cache(cache_key)
    if cached is not None:
        return cached

    paths: List[List[int]] = []

    # Step 1 – get announced prefixes for this ASN
    prefixes = _fetch_prefixes_for_asn(asn)
    if not prefixes:
        _write_cache(cache_key, paths)
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
    except Exception as e:
        print(f"[ASPath] Error fetching looking-glass for {sample_prefix}: {e}")

    _write_cache(cache_key, paths)
    return paths


def _fetch_prefixes_for_asn(asn: int) -> List[str]:
    """Return a list of prefixes originated by *asn* (cached)."""
    cache_key = _cache_key("pfx", str(asn))
    cached = _read_cache(cache_key)
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
    except Exception as e:
        print(f"[ASPath] Error fetching prefixes for AS{asn}: {e}")

    _write_cache(cache_key, prefixes)
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


def _analyze_facility_paths(
    facility_asns: List[int],
    local_asns: List[int],
) -> Dict[int, int]:
    """
    For every ASN present at a facility, fetch its AS-path data and extract
    the first hop immediately adjacent to Zenlayer.

    Returns {facility_asn: first_hop_asn} for every ASN where a first hop
    was found.
    """
    local_set = set(local_asns)
    first_hops: Dict[int, int] = {}

    for asn in facility_asns:
        if asn in local_set:
            continue
        paths = _fetch_as_path(asn)
        for path in paths:
            hop = _extract_first_hop(path, local_set)
            if hop is not None:
                first_hops[asn] = hop
                break  # one confirmed hop is enough per destination ASN

    return first_hops


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

def fetch_peeringdb(endpoint: str) -> List[Dict[str, Any]]:
    """Fetches data from PeeringDB with 24-hour file cache."""
    clean_endpoint = endpoint.strip("/")
    cache_key = _cache_key("pdb", clean_endpoint)

    cached = _read_cache(cache_key)
    if cached is not None:
        return cached

    url = f"{PEERINGDB_BASE}/{clean_endpoint}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", [])
        _write_cache(cache_key, data)
        return data
    except requests.exceptions.RequestException as e:
        print(f"PeeringDB API Error: {e}")
        return []

@app.on_event("startup")
def initialize_footprint():
    """Build the Zenlayer facility/city/metro map based on dynamic config."""
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
    fac_ids = list(set([nf["fac_id"] for nf in netfacs]))
    
    if fac_ids:
        facilities = fetch_peeringdb(f"fac?id__in={','.join(map(str, fac_ids))}")
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
    
    print(f"Zenlayer BGP Audit: Footprint loaded. ASNs: {asns}, Metros: {len(zenlayer_state['unique_metros'])}, Cities: {len(zenlayer_state['unique_cities'])}")
@app.get("/", response_class=HTMLResponse)
@app.get("", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    """Serve the main dashboard UI."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "metros": zenlayer_state.get("unique_metros", []),
        "cities": zenlayer_state.get("unique_cities", []),
        "facilities": zenlayer_state.get("facilities", [])
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
    initialize_footprint()
    return {"status": "success", "message": "Settings updated."}

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
        net_ids = [nf["net_id"] for nf in netfacs]
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
            target_fac_ids = [f["id"] for f in zenlayer_state["facilities"] if f.get("city") == location_name]
            print(f"[Discovery] City '{location_name}': found {len(target_fac_ids)} facilities: {target_fac_ids}")

        if target_fac_ids:
            fac_query = ",".join(map(str, target_fac_ids))
            netfacs = fetch_peeringdb(f"netfac?fac_id__in={fac_query}")
            net_ids = list(set([nf["net_id"] for nf in netfacs]))
    
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
    """Return a summary of network presence including direct peer highlights."""
    try:
        config = load_config()
        zenlayer_asns = config.get("ASNS", DEFAULT_CONFIG["ASNS"])

        all_nets = _get_discovery_data(fac_id, location, location_type, "all")
        upstream = [n for n in all_nets if n["info_type"] == "NSP" or "Transit" in n["info_type"]]
        content = [n for n in all_nets if n["info_type"] in ("Content",)]
        eyeball = [n for n in all_nets if "Eyeball" in n["info_type"]]

        # AS-Path analysis: find direct peers among all facility networks
        all_asns = [n["asn"] for n in all_nets if n.get("asn")]
        first_hops = _analyze_facility_paths(all_asns, zenlayer_asns)
        direct_peer_asns = set(first_hops.values())

        direct_neighbors = [
            {"asn": n["asn"], "name": n["name"]}
            for n in all_nets if n["asn"] in direct_peer_asns
        ]

        return {
            "total": len(all_nets),
            "upstream_count": len(upstream),
            "content_count": len(content),
            "eyeball_count": len(eyeball),
            "direct_peers": sorted(direct_neighbors, key=lambda x: x["name"]),
            "direct_peer_count": len(direct_neighbors),
        }
    except Exception as e:
        print(f"[API] Summary error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/routing", response_class=HTMLResponse)
async def routing_flow_page(request: Request):
    """Serve the hierarchical routing flow visualization page."""
    return templates.TemplateResponse("routing.html", {
        "request": request,
        "cities": zenlayer_state.get("unique_cities", []),
        "facilities": zenlayer_state.get("facilities", [])
    })

def _expand_as_set(as_set: str) -> List[Dict[str, Any]]:
    """
    Expand an AS-SET to its member ASNs using IRR data.
    Results are cached to disk for 24 hours.
    """
    if not as_set or as_set == "":
        return []

    as_set = as_set.strip().upper()
    cache_key = _cache_key("irr", as_set)

    cached = _read_cache(cache_key)
    if cached is not None:
        return cached

    try:
        url = f"https://irrexplorer.nlnog.net/api/sets/member_of/{as_set}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            members = []
            for item in data.get("directMembers", [])[:50]:
                if item.startswith("AS") and item[2:].isdigit():
                    members.append({"asn": int(item[2:]), "name": item})
            _write_cache(cache_key, members)
            return members
    except Exception as e:
        print(f"[IRR] Error expanding {as_set}: {e}")

    return []

@app.get("/api/routing-flow")
async def get_routing_flow(
    location: str,
    location_type: str = "city",
    expand_sets: bool = False
):
    """
    Build hierarchical routing flow data using AS-Path Frequency Analysis.

    For every network at a facility we fetch real AS-path data via RIPEstat
    and extract the first hop adjacent to Zenlayer.  Networks are then
    grouped into a 3-level tree:

      Level 0 (Root)  – Zenlayer (AS21859 / AS4229)
      Level 1         – Direct Peers: ASNs that appear as the first hop
                        adjacent to a Zenlayer ASN in any observed path
      Level 2         – Downstream: facility networks whose first hop is
                        one of the Level-1 Direct Peers
      Level 1 (also)  – Unresolved: networks where no Zenlayer-adjacent
                        hop was found (shown as "Transit")
    """
    try:
        config = load_config()
        zenlayer_asns = config.get("ASNS", DEFAULT_CONFIG["ASNS"])
        local_set = set(zenlayer_asns)

        # Get facilities for this location
        target_fac_ids = []
        if location_type == "city":
            target_fac_ids = [f["id"] for f in zenlayer_state["facilities"] if f.get("city") == location]
        else:
            mapping = config.get("METRO_MAP", {})
            cities_in_metro = [city for city, m in mapping.items() if m == location]
            target_fac_ids = [f["id"] for f in zenlayer_state["facilities"] if f.get("city") in cities_in_metro]

        if not target_fac_ids:
            return {"error": "No facilities found for location"}

        # Get all networks at these facilities
        fac_query = ",".join(map(str, target_fac_ids))
        netfacs = fetch_peeringdb(f"netfac?fac_id__in={fac_query}")
        net_ids = list(set([nf["net_id"] for nf in netfacs]))

        all_nets = []
        batch_size = 50
        for i in range(0, len(net_ids), batch_size):
            chunk = net_ids[i:i + batch_size]
            nets = fetch_peeringdb(f"net?id__in={','.join(map(str, chunk))}")
            all_nets.extend(nets)

        # Index every non-Zenlayer facility network by ASN
        facility_nets = {}
        for net in all_nets:
            asn = net.get("asn")
            if asn and asn not in local_set:
                facility_nets[asn] = net

        # ------------------------------------------------------------------
        # AS-Path Frequency Analysis
        # For each facility ASN, fetch paths and find the first hop adjacent
        # to Zenlayer.  Result: {destination_asn: first_hop_asn}
        # ------------------------------------------------------------------
        first_hops = _analyze_facility_paths(
            list(facility_nets.keys()), zenlayer_asns
        )
        print(f"[RoutingFlow] {location}: {len(first_hops)}/{len(facility_nets)} ASNs resolved a first hop")

        # ------------------------------------------------------------------
        # Majority-Hop Grouping
        # Any ASN that appears as a first_hop for at least one facility
        # network is promoted to Level 1 (Direct Peer).  Every destination
        # that routes through it becomes its Level 2 child.
        # ------------------------------------------------------------------
        # Collect {hop_asn: [dest_asn, ...]}
        hop_to_dests: Dict[int, List[int]] = {}
        for dest_asn, hop_asn in first_hops.items():
            hop_to_dests.setdefault(hop_asn, []).append(dest_asn)

        direct_peer_nodes: Dict[int, dict] = {}
        assigned_asns = set(local_set)

        for hop_asn, dest_asns in hop_to_dests.items():
            # Build the Direct Peer node
            if hop_asn in facility_nets:
                net = facility_nets[hop_asn]
                name = net.get("name", f"AS{hop_asn}")
                info_type = net.get("info_type", "")
            else:
                name = f"AS{hop_asn}"
                info_type = "NSP"

            children = []
            for d_asn in sorted(dest_asns):
                if d_asn == hop_asn or d_asn in local_set:
                    continue
                d_net = facility_nets.get(d_asn)
                if d_net:
                    children.append({
                        "asn": d_asn,
                        "name": d_net.get("name", f"AS{d_asn}"),
                        "info_type": d_net.get("info_type", ""),
                        "is_direct": False,
                        "category": "Downstream",
                    })
                    assigned_asns.add(d_asn)

            children.sort(key=lambda x: x["name"])

            direct_peer_nodes[hop_asn] = {
                "asn": hop_asn,
                "name": name,
                "info_type": info_type,
                "is_direct": True,
                "category": "Direct Peer",
                "children": children,
            }
            assigned_asns.add(hop_asn)

        # ------------------------------------------------------------------
        # Unresolved networks → Level 1 as "Transit"
        # These are facility networks where no Zenlayer-adjacent hop was
        # found in the observed AS paths.
        # ------------------------------------------------------------------
        transit_nodes = []
        for asn, net in facility_nets.items():
            if asn in assigned_asns:
                continue
            transit_nodes.append({
                "asn": asn,
                "name": net.get("name", f"AS{asn}"),
                "info_type": net.get("info_type", ""),
                "is_direct": False,
                "category": "Transit",
                "children": []
            })

        direct_list = sorted(direct_peer_nodes.values(), key=lambda x: x["name"])
        transit_nodes.sort(key=lambda x: x["name"])
        downstream_total = sum(len(p["children"]) for p in direct_list)

        tree = {
            "name": f"Zenlayer ({', '.join(f'AS{asn}' for asn in zenlayer_asns)})",
            "asn": zenlayer_asns[0],
            "location": location,
            "direct_peer_count": len(direct_list),
            "transit_count": len(transit_nodes),
            "downstream_count": downstream_total,
            "children": direct_list + transit_nodes
        }

        return tree

    except Exception as e:
        print(f"[API] Routing flow error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
