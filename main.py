
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

def _fetch_bgp_neighbors(asn: int) -> set:
    """
    Fetch all BGP neighbor ASNs for an ASN.
    Uses BGPView /peers (all BGP neighbors) as the primary source, with
    RIPEstat asn-neighbours as a fallback.  Results are cached for 24 hours.

    The /peers endpoint is used instead of /upstreams because large transit
    providers like Zenlayer may report zero upstreams in BGPView while still
    having hundreds of active BGP peers.
    """
    cache_key = _cache_key("bgp_neighbors", str(asn))
    cached = _read_cache(cache_key)
    if cached is not None:
        return set(cached)

    neighbor_asns = set()
    headers = {"User-Agent": "bgp-audit/1.0"}

    # --- Primary: BGPView /peers ---
    try:
        url = f"{BGPVIEW_BASE}/asn/{asn}/peers"
        resp = requests.get(url, timeout=15, headers=headers)
        if resp.status_code == 200 and resp.text.strip():
            data = resp.json().get("data", {})
            for p in data.get("ipv4_peers", []):
                p_asn = p.get("asn")
                if p_asn:
                    neighbor_asns.add(p_asn)
            for p in data.get("ipv6_peers", []):
                p_asn = p.get("asn")
                if p_asn:
                    neighbor_asns.add(p_asn)
            print(f"[BGPView] AS{asn} /peers returned {len(neighbor_asns)} neighbors")
    except Exception as e:
        print(f"[BGPView] Error fetching peers for AS{asn}: {e}")

    # --- Fallback: RIPEstat asn-neighbours ---
    if not neighbor_asns:
        try:
            url = f"https://stat.ripe.net/data/asn-neighbours/data.json?resource=AS{asn}"
            resp = requests.get(url, timeout=15, headers=headers)
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json().get("data", {})
                for n in data.get("neighbours", []):
                    n_asn = n.get("asn")
                    if n_asn:
                        neighbor_asns.add(n_asn)
                print(f"[RIPEstat] AS{asn} returned {len(neighbor_asns)} neighbors (fallback)")
        except Exception as e:
            print(f"[RIPEstat] Error fetching neighbors for AS{asn}: {e}")

    if not neighbor_asns:
        print(f"[BGP] WARNING: No neighbors found for AS{asn} from any source")

    _write_cache(cache_key, list(neighbor_asns))
    return neighbor_asns


def _get_direct_peers(local_asns: List[int]) -> set:
    """
    Build the set of ASNs that have a direct BGP session with any of our
    local ASNs.  The caller is expected to filter the result against the
    facility network list so that only co-located peers remain.
    """
    local_set = set(local_asns)
    direct_asns = set()
    for asn in local_asns:
        neighbors = _fetch_bgp_neighbors(asn)
        direct_asns.update(neighbors - local_set)
    print(f"[BGP] Total direct peer ASNs across {len(local_asns)} local ASNs: {len(direct_asns)}")
    return direct_asns


def _fetch_downstreams(asn: int) -> set:
    """
    Fetch downstream ASNs for a network via BGPView.
    These are networks that transit through this ASN — i.e. the connections
    this peer provides. Results are cached to disk for 24 hours.
    """
    cache_key = _cache_key("bgp_down", str(asn))
    cached = _read_cache(cache_key)
    if cached is not None:
        return set(cached)

    downstream_asns = set()
    try:
        url = f"{BGPVIEW_BASE}/asn/{asn}/downstreams"
        response = requests.get(url, timeout=15, headers={"User-Agent": "bgp-audit/1.0"})
        if response.status_code == 200:
            data = response.json().get("data", {})
            for d in data.get("ipv4_downstreams", []):
                d_asn = d.get("asn")
                if d_asn:
                    downstream_asns.add(d_asn)
            for d in data.get("ipv6_downstreams", []):
                d_asn = d.get("asn")
                if d_asn:
                    downstream_asns.add(d_asn)
    except Exception as e:
        print(f"[BGPView] Error fetching downstreams for AS{asn}: {e}")

    _write_cache(cache_key, list(downstream_asns))
    return downstream_asns


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
    - Level 0 (Root): Zenlayer ASNs
    - Level 1: Direct peers — first-hop neighbors from AS path analysis
    - Level 2: Networks at this facility that route through each direct peer
    - Level 1 (also): Remaining NSP/Transit networks not reached via any
      detected direct peer (shown as "Transit")
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
        # Level 1: Identify first-hop neighbors via AS path analysis
        # ------------------------------------------------------------------
        direct_peer_asns = _get_direct_peers(zenlayer_asns)

        direct_peer_nodes = {}
        for asn in direct_peer_asns:
            if asn in facility_nets:
                net = facility_nets[asn]
                direct_peer_nodes[asn] = {
                    "asn": asn,
                    "name": net.get("name"),
                    "info_type": net.get("info_type", ""),
                    "irr_as_set": net.get("irr_as_set", ""),
                    "is_direct": True,
                    "category": "Direct Peer",
                    "children": []
                }

        # ------------------------------------------------------------------
        # Level 2: For each direct peer, find their downstream networks
        # that are ALSO present at this facility
        # ------------------------------------------------------------------
        assigned_asns = set(direct_peer_nodes.keys()) | local_set

        for peer_asn, peer_node in direct_peer_nodes.items():
            downstream_asns = _fetch_downstreams(peer_asn)

            # Optionally merge in AS-SET expansion members
            if expand_sets:
                irr_as_set = peer_node.get("irr_as_set", "")
                if irr_as_set:
                    as_set = irr_as_set.split()[0]
                    if as_set:
                        for m in _expand_as_set(as_set):
                            downstream_asns.add(m["asn"])

            # Match downstream ASNs against facility networks
            for asn, net in facility_nets.items():
                if asn in assigned_asns:
                    continue
                if asn in downstream_asns:
                    peer_node["children"].append({
                        "asn": asn,
                        "name": net.get("name"),
                        "info_type": net.get("info_type", ""),
                        "is_direct": False,
                        "category": "Downstream",
                    })
                    assigned_asns.add(asn)

            peer_node["children"].sort(key=lambda x: x["name"])

        # ------------------------------------------------------------------
        # Remaining unassigned NSP/Transit networks → Level 1 as "Transit"
        # ------------------------------------------------------------------
        transit_nodes = []
        for asn, net in facility_nets.items():
            if asn in assigned_asns:
                continue
            info_type = net.get("info_type", "")
            if info_type == "NSP" or "Transit" in info_type:
                transit_nodes.append({
                    "asn": asn,
                    "name": net.get("name"),
                    "info_type": info_type,
                    "irr_as_set": net.get("irr_as_set", ""),
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
