
import os
import json
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

@functools.lru_cache(maxsize=128)
def fetch_peeringdb(endpoint: str) -> List[Dict[str, Any]]:
    """Fetches data from PeeringDB with memoization."""
    clean_endpoint = endpoint.strip("/")
    url = f"{PEERINGDB_BASE}/{clean_endpoint}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json().get("data", [])
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
    fetch_peeringdb.cache_clear()
    _get_discovery_data.cache_clear()
    initialize_footprint()
    return {"status": "success", "message": "Settings updated."}

@app.post("/api/cache/clear")
async def clear_cache():
    """Clear discovery cache for debugging."""
    _get_discovery_data.cache_clear()
    print("[API] Discovery cache cleared")
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

@app.get("/routing", response_class=HTMLResponse)
async def routing_flow_page(request: Request):
    """Serve the hierarchical routing flow visualization page."""
    return templates.TemplateResponse("routing.html", {
        "request": request,
        "cities": zenlayer_state.get("unique_cities", []),
        "facilities": zenlayer_state.get("facilities", [])
    })

@functools.lru_cache(maxsize=256)
def _expand_as_set(as_set: str) -> List[Dict[str, Any]]:
    """
    Expand an AS-SET to its member ASNs using IRR data.
    Uses the RIPE WHOIS-style query via public API.
    """
    if not as_set or as_set == "":
        return []

    # Clean up the AS-SET name
    as_set = as_set.strip().upper()

    # Try to fetch from IRR Explorer API (publicly available)
    try:
        # Use bgpstuff.net API for AS-SET expansion (free, no auth required)
        url = f"https://irrexplorer.nlnog.net/api/sets/member_of/{as_set}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Return list of member ASNs
            members = []
            for item in data.get("directMembers", [])[:50]:  # Limit to 50 for performance
                if item.startswith("AS") and item[2:].isdigit():
                    members.append({"asn": int(item[2:]), "name": item})
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

        # Filter to transit/upstream providers (NSP or Transit types)
        direct_peers = []
        for net in all_nets:
            # Skip Zenlayer's own ASNs
            if net.get("asn") in zenlayer_asns:
                continue

            info_type = net.get("info_type", "")
            is_nsp = info_type == "NSP"
            is_transit = "Transit" in info_type

            if is_nsp or is_transit:
                peer_data = {
                    "asn": net.get("asn"),
                    "name": net.get("name"),
                    "info_type": info_type,
                    "irr_as_set": net.get("irr_as_set", ""),
                    "children": []
                }

                # Optionally expand AS-SET
                if expand_sets and peer_data["irr_as_set"]:
                    # Take first AS-SET if multiple are listed
                    as_set = peer_data["irr_as_set"].split()[0] if peer_data["irr_as_set"] else ""
                    if as_set:
                        peer_data["children"] = _expand_as_set(as_set)

                direct_peers.append(peer_data)

        # Sort by name
        direct_peers.sort(key=lambda x: x["name"])

        # Build the tree structure
        tree = {
            "name": f"Zenlayer ({', '.join(f'AS{asn}' for asn in zenlayer_asns)})",
            "asn": zenlayer_asns[0],
            "location": location,
            "children": direct_peers
        }

        return tree

    except Exception as e:
        print(f"[API] Routing flow error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
