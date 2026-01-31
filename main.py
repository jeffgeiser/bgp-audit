
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

BGPVIEW_BASE = "https://api.bgpview.io"

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

def _fetch_bgp_peers(asn: int) -> set:
    """
    Fetch direct BGP peers for an ASN via BGPView.
    Returns a set of peer ASNs (first-hop neighbors).
    Results are cached to disk for 24 hours.
    """
    cache_key = _cache_key("bgp_peers", str(asn))
    cached = _read_cache(cache_key)
    if cached is not None:
        return set(cached)

    peer_asns = set()
    try:
        url = f"{BGPVIEW_BASE}/asn/{asn}/peers"
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json().get("data", {})
            for p in data.get("ipv4_peers", []):
                peer_asn = p.get("asn")
                if peer_asn:
                    peer_asns.add(peer_asn)
            for p in data.get("ipv6_peers", []):
                peer_asn = p.get("asn")
                if peer_asn:
                    peer_asns.add(peer_asn)
    except Exception as e:
        print(f"[BGPView] Error fetching peers for AS{asn}: {e}")

    _write_cache(cache_key, list(peer_asns))
    return peer_asns


def _get_direct_peers(local_asns: List[int]) -> set:
    """
    Build the combined set of direct BGP peers across all local ASNs.
    Excludes the local ASNs themselves from the result.
    """
    all_peers = set()
    local_set = set(local_asns)
    for asn in local_asns:
        all_peers.update(_fetch_bgp_peers(asn))
    return all_peers - local_set


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
    if not os.path.exists(CONFIG_FILE):
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

        # Dynamically identify direct BGP peers
        direct_peer_asns = _get_direct_peers(zenlayer_asns)
        direct_neighbors = [
            {"asn": n["asn"], "name": n["name"]}
            for n in upstream if n["asn"] in direct_peer_asns
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
    Build hierarchical routing flow data for visualization.

    Structure:
    - Root: Zenlayer ASNs
    - Level 1: Direct peers at the location (transit/upstream providers)
    - Level 2: (Optional) Expanded AS-SET members from each peer
    """
    try:
        config = load_config()
        zenlayer_asns = config.get("ASNS", DEFAULT_CONFIG["ASNS"])

        # Get facilities for this location
        target_fac_ids = []
        if location_type == "city":
            target_fac_ids = [f["id"] for f in zenlayer_state["facilities"] if f.get("city") == location]
        else:
            # Metro lookup
            mapping = config.get("METRO_MAP", {})
            cities_in_metro = [city for city, m in mapping.items() if m == location]
            target_fac_ids = [f["id"] for f in zenlayer_state["facilities"] if f.get("city") in cities_in_metro]

        if not target_fac_ids:
            return {"error": "No facilities found for location"}

        # Get all networks at these facilities
        fac_query = ",".join(map(str, target_fac_ids))
        netfacs = fetch_peeringdb(f"netfac?fac_id__in={fac_query}")
        net_ids = list(set([nf["net_id"] for nf in netfacs]))

        # Fetch network details
        all_nets = []
        batch_size = 50
        for i in range(0, len(net_ids), batch_size):
            chunk = net_ids[i:i + batch_size]
            nets = fetch_peeringdb(f"net?id__in={','.join(map(str, chunk))}")
            all_nets.extend(nets)

        # Dynamically identify direct BGP peers via BGPView
        direct_peer_asns = _get_direct_peers(zenlayer_asns)

        # Classify peers: Direct Peer (first-hop BGP neighbor) vs Transit
        direct_peers = []
        transit_peers = []

        for net in all_nets:
            asn = net.get("asn")
            if asn in zenlayer_asns:
                continue

            info_type = net.get("info_type", "")
            is_nsp = info_type == "NSP"
            is_transit = "Transit" in info_type

            if is_nsp or is_transit:
                is_direct = asn in direct_peer_asns
                peer_data = {
                    "asn": asn,
                    "name": net.get("name"),
                    "info_type": info_type,
                    "irr_as_set": net.get("irr_as_set", ""),
                    "is_direct": is_direct,
                    "category": "Direct Peer" if is_direct else "Transit",
                    "children": []
                }

                if expand_sets and peer_data["irr_as_set"]:
                    as_set = peer_data["irr_as_set"].split()[0] if peer_data["irr_as_set"] else ""
                    if as_set:
                        peer_data["children"] = _expand_as_set(as_set)

                if is_direct:
                    direct_peers.append(peer_data)
                else:
                    transit_peers.append(peer_data)

        direct_peers.sort(key=lambda x: x["name"])
        transit_peers.sort(key=lambda x: x["name"])

        # Build the tree: Direct Peers first, then Transit
        tree = {
            "name": f"Zenlayer ({', '.join(f'AS{asn}' for asn in zenlayer_asns)})",
            "asn": zenlayer_asns[0],
            "location": location,
            "direct_peer_count": len(direct_peers),
            "transit_count": len(transit_peers),
            "children": direct_peers + transit_peers
        }

        return tree

    except Exception as e:
        print(f"[API] Routing flow error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
