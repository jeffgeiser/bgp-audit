
import os
import json
import time
import hashlib
import requests
import functools
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

        # AS-Path analysis: find direct peers from Zenlayer's own paths
        direct_peer_asns, _ = _analyze_zenlayer_paths(zenlayer_asns)
        facility_asns = {n["asn"] for n in all_nets if n.get("asn")}
        direct_at_facility = direct_peer_asns & facility_asns

        direct_neighbors = [
            {"asn": n["asn"], "name": n["name"]}
            for n in all_nets if n["asn"] in direct_at_facility
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
    Build hierarchical routing flow data using AS-Path Frequency Analysis
    with PeeringDB verification.

    Tree structure:
      Level 0 (Root) – Zenlayer Origins
      Level 1        – Direct Peers (Verified Local | External 1-Hop)
        Level 2      – Downstream: networks that route through this peer
        Level 2      – Reachable Transit: unresolved networks assigned to
                       this peer via extended path analysis
      Level 1        – Unresolved: networks not assignable to any peer
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

        facility_asn_set = set(facility_nets.keys())

        # ------------------------------------------------------------------
        # PeeringDB Verification: find IXes at these facilities so we can
        # verify co-location for peers that share an IX with Zenlayer even
        # if they are registered at a different facility.
        # ------------------------------------------------------------------
        zenlayer_net_ids = [n["id"] for n in zenlayer_state.get("networks", [])]
        ix_at_facs = fetch_peeringdb(f"ixfac?fac_id__in={fac_query}")
        local_ix_ids = set(ix.get("ix_id") for ix in ix_at_facs if ix.get("ix_id"))

        zl_ix_ids: set = set()
        for nid in zenlayer_net_ids:
            for rec in fetch_peeringdb(f"netixlan?net_id={nid}"):
                ix_id = rec.get("ix_id")
                if ix_id:
                    zl_ix_ids.add(ix_id)
        zenlayer_local_ixes = local_ix_ids & zl_ix_ids

        # Build a lookup: {ix_id: ix_name}
        ix_names: Dict[int, str] = {}
        if zenlayer_local_ixes:
            ix_data = fetch_peeringdb(
                f"ix?id__in={','.join(map(str, zenlayer_local_ixes))}"
            )
            for ix in ix_data:
                ix_names[ix["id"]] = ix.get("name", f"IX-{ix['id']}")

        # ------------------------------------------------------------------
        # AS-Path Frequency Analysis  (2 API calls per local ASN, cached)
        # ------------------------------------------------------------------
        direct_peer_asns, peer_downstreams = _analyze_zenlayer_paths(
            zenlayer_asns
        )

        facility_direct = direct_peer_asns & facility_asn_set

        print(
            f"[RoutingFlow] {location}: "
            f"{len(facility_direct)} direct peers at facility "
            f"(of {len(direct_peer_asns)} global)"
        )

        # ------------------------------------------------------------------
        # Build Level 1 nodes  (Direct Peers)
        # ------------------------------------------------------------------
        direct_peer_nodes: Dict[int, dict] = {}
        assigned_asns = set(local_set)

        for hop_asn in sorted(facility_direct):
            net = facility_nets[hop_asn]

            # Level 2: downstream ASNs via path analysis
            downstream_at_fac = (
                peer_downstreams.get(hop_asn, set()) & facility_asn_set
            )

            children = []
            for d_asn in sorted(downstream_at_fac):
                if d_asn in assigned_asns or d_asn == hop_asn:
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

            # PeeringDB Verified – at the same facility
            direct_peer_nodes[hop_asn] = {
                "asn": hop_asn,
                "name": net.get("name", f"AS{hop_asn}"),
                "info_type": net.get("info_type", ""),
                "is_direct": True,
                "verified": True,
                "verification": "PeeringDB Facility",
                "category": "Direct Peer",
                "children": children,
                "transit_children": [],
            }
            assigned_asns.add(hop_asn)

        # External 1-Hop peers (direct peer globally but NOT at this
        # facility).  Check IX co-location before labelling.
        external_direct = direct_peer_asns - facility_asn_set - local_set
        for hop_asn in sorted(external_direct):
            # Lightweight IX membership check (cached)
            verified = False
            verification = "External 1-Hop"
            shared_ix: List[str] = []

            peer_nets = fetch_peeringdb(f"net?asn={hop_asn}")
            if peer_nets and zenlayer_local_ixes:
                pnet_id = peer_nets[0]["id"]
                peer_ixlans = fetch_peeringdb(f"netixlan?net_id={pnet_id}")
                peer_ix_set = set(r.get("ix_id") for r in peer_ixlans)
                common = peer_ix_set & zenlayer_local_ixes
                if common:
                    verified = True
                    verification = "Shared IX"
                    shared_ix = [ix_names.get(ix, f"IX-{ix}") for ix in common]

            # Still include their downstream networks at the facility
            downstream_at_fac = (
                peer_downstreams.get(hop_asn, set()) & facility_asn_set
            )
            children = []
            for d_asn in sorted(downstream_at_fac):
                if d_asn in assigned_asns or d_asn == hop_asn:
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

            name = peer_nets[0].get("name", f"AS{hop_asn}") if peer_nets else f"AS{hop_asn}"
            info_type = peer_nets[0].get("info_type", "") if peer_nets else "NSP"

            direct_peer_nodes[hop_asn] = {
                "asn": hop_asn,
                "name": name,
                "info_type": info_type,
                "is_direct": True,
                "verified": verified,
                "verification": verification,
                "shared_ix": shared_ix,
                "category": "Direct Peer",
                "children": children,
                "transit_children": [],
            }
            assigned_asns.add(hop_asn)

        # ------------------------------------------------------------------
        # Nest unresolved transit networks under their nearest direct peer
        # by checking if the transit ASN appears in any peer's downstream
        # set from the global path data.
        # ------------------------------------------------------------------
        unresolved_asns = set()
        for asn in facility_nets:
            if asn not in assigned_asns:
                unresolved_asns.add(asn)

        for t_asn in list(unresolved_asns):
            for peer_asn, ds_set in peer_downstreams.items():
                if t_asn in ds_set and peer_asn in direct_peer_nodes:
                    t_net = facility_nets[t_asn]
                    direct_peer_nodes[peer_asn]["transit_children"].append({
                        "asn": t_asn,
                        "name": t_net.get("name", f"AS{t_asn}"),
                        "info_type": t_net.get("info_type", ""),
                        "is_direct": False,
                        "category": "Transit Reachable",
                    })
                    assigned_asns.add(t_asn)
                    unresolved_asns.discard(t_asn)
                    break

        # Sort transit children
        for node in direct_peer_nodes.values():
            node["transit_children"].sort(key=lambda x: x["name"])

        # ------------------------------------------------------------------
        # Truly unresolved → single collapsible "Unresolved" group
        # ------------------------------------------------------------------
        unresolved_children = []
        for asn in sorted(unresolved_asns):
            net = facility_nets[asn]
            unresolved_children.append({
                "asn": asn,
                "name": net.get("name", f"AS{asn}"),
                "info_type": net.get("info_type", ""),
                "is_direct": False,
                "category": "Unresolved",
            })

        # ------------------------------------------------------------------
        # Assemble tree
        # ------------------------------------------------------------------
        direct_list = sorted(direct_peer_nodes.values(), key=lambda x: x["name"])
        downstream_total = sum(len(p["children"]) for p in direct_list)
        transit_nested = sum(len(p["transit_children"]) for p in direct_list)

        tree = {
            "name": f"Zenlayer ({', '.join(f'AS{asn}' for asn in zenlayer_asns)})",
            "asn": zenlayer_asns[0],
            "location": location,
            "direct_peer_count": len(direct_list),
            "downstream_count": downstream_total,
            "transit_nested_count": transit_nested,
            "unresolved_count": len(unresolved_children),
            "children": direct_list,
            "unresolved": unresolved_children,
        }

        return tree

    except Exception as e:
        print(f"[API] Routing flow error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# PDF Export
# ---------------------------------------------------------------------------

def _build_routing_pdf(tree: dict, location: str) -> bytes:
    """Render a customer-ready PDF for a routing-flow tree."""
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from datetime import datetime, timezone

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    # Colours
    zl_navy = colors.HexColor("#00205B")
    zl_blue = colors.HexColor("#00A9E0")
    peer_blue = colors.HexColor("#2563eb")
    verified_green = colors.HexColor("#16a34a")
    light_blue = colors.HexColor("#eff6ff")
    light_green = colors.HexColor("#dcfce7")
    ds_blue = colors.HexColor("#dbeafe")
    transit_bg = colors.HexColor("#f1f5f9")

    # Custom styles
    title_style = ParagraphStyle(
        "ZLTitle", parent=styles["Title"],
        textColor=zl_navy, fontSize=18, spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "ZLSub", parent=styles["Normal"],
        textColor=colors.HexColor("#64748b"), fontSize=10, spaceAfter=12,
    )
    section_style = ParagraphStyle(
        "ZLSection", parent=styles["Heading2"],
        textColor=zl_navy, fontSize=13, spaceBefore=16, spaceAfter=6,
    )
    cell_style = ParagraphStyle(
        "Cell", parent=styles["Normal"], fontSize=9, leading=12,
    )
    cell_bold = ParagraphStyle(
        "CellBold", parent=cell_style, fontName="Helvetica-Bold",
    )
    cell_mono = ParagraphStyle(
        "CellMono", parent=cell_style, fontName="Courier", fontSize=9,
    )
    cell_small = ParagraphStyle(
        "CellSmall", parent=cell_style, fontSize=8,
        textColor=colors.HexColor("#64748b"),
    )

    # Header
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    elements.append(Paragraph("Zenlayer BGP Routing Flow", title_style))
    elements.append(Paragraph(f"{location}  &mdash;  Generated {now}", subtitle_style))

    # Summary — use new tree fields
    direct_peers = tree.get("children", [])
    unresolved = tree.get("unresolved", [])
    downstream_total = tree.get("downstream_count", 0)
    transit_nested = tree.get("transit_nested_count", 0)

    summary_data = [
        ["Direct Peers", str(tree.get("direct_peer_count", len(direct_peers)))],
        ["Downstream Networks", str(downstream_total)],
        ["Transit Reachable", str(transit_nested)],
        ["Unresolved", str(tree.get("unresolved_count", len(unresolved)))],
        [
            "Total Facility Networks",
            str(len(direct_peers) + downstream_total + transit_nested + len(unresolved)),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[2.5 * inch, 1.2 * inch])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ]))
    elements.append(summary_table)

    # Direct Peers section
    if direct_peers:
        elements.append(Paragraph("Direct Peers", section_style))

        for peer in sorted(direct_peers, key=lambda p: p.get("name", "")):
            peer_children = peer.get("children", [])
            peer_transit = peer.get("transit_children", [])

            # Verification label
            verified = peer.get("verified", False)
            verification = peer.get("verification", "")
            if verified:
                status_text = f'<font color="#16a34a"><b>{verification}</b></font>'
            else:
                status_text = f'<font color="#94a3b8">{verification}</font>'

            # Shared IX detail
            shared_ix = peer.get("shared_ix", [])
            ix_detail = ""
            if shared_ix:
                ix_detail = f" ({', '.join(shared_ix)})"

            # Summary line: "3 Downstream, 12 Transit Reachable"
            counts = []
            if peer_children:
                counts.append(f"{len(peer_children)} downstream")
            if peer_transit:
                counts.append(f"{len(peer_transit)} transit reachable")
            count_text = ", ".join(counts) if counts else "no downstream"

            rows = [[
                Paragraph(f"<b>AS{peer['asn']}</b>", cell_mono),
                Paragraph(f"<b>{peer.get('name', '')}</b>", cell_bold),
                Paragraph(status_text + ix_detail, cell_style),
                Paragraph(count_text, cell_small),
            ]]
            peer_bg = light_green if verified else light_blue
            peer_border = verified_green if verified else colors.HexColor("#94a3b8")
            t = Table(rows, colWidths=[0.9 * inch, 2.5 * inch, 1.6 * inch, 1.2 * inch])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), peer_bg),
                ("BOX", (0, 0), (-1, -1), 0.5, peer_border),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]))
            elements.append(Spacer(1, 4))
            elements.append(t)

            # Downstream table under this peer
            if peer_children:
                ds_rows = []
                for ds in sorted(peer_children, key=lambda d: d.get("name", "")):
                    ds_rows.append([
                        Paragraph(f"AS{ds['asn']}", cell_mono),
                        Paragraph(ds.get("name", ""), cell_style),
                        Paragraph(ds.get("info_type", ""), cell_style),
                    ])
                ds_table = Table(
                    ds_rows,
                    colWidths=[0.9 * inch, 3.0 * inch, 1.3 * inch],
                )
                ds_table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), ds_blue),
                    ("BOX", (0, 0), (-1, -1), 0.25, colors.HexColor("#93c5fd")),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#bfdbfe")),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (0, -1), 20),
                    ("LEFTPADDING", (1, 0), (-1, -1), 6),
                ]))
                elements.append(ds_table)

            # Transit reachable table under this peer
            if peer_transit:
                tr_rows = []
                for tr in sorted(peer_transit, key=lambda t: t.get("name", "")):
                    tr_rows.append([
                        Paragraph(f"AS{tr['asn']}", cell_mono),
                        Paragraph(tr.get("name", ""), cell_style),
                        Paragraph(tr.get("info_type", ""), cell_small),
                    ])
                tr_table = Table(
                    tr_rows,
                    colWidths=[0.9 * inch, 3.0 * inch, 1.3 * inch],
                )
                tr_table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), transit_bg),
                    ("BOX", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e8f0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (0, -1), 20),
                    ("LEFTPADDING", (1, 0), (-1, -1), 6),
                ]))
                elements.append(tr_table)

    # Unresolved section
    if unresolved:
        elements.append(Paragraph("Unresolved Networks", section_style))
        t_rows = [
            [
                Paragraph("<b>ASN</b>", cell_bold),
                Paragraph("<b>Name</b>", cell_bold),
                Paragraph("<b>Type</b>", cell_bold),
            ]
        ]
        for node in sorted(unresolved, key=lambda n: n.get("name", "")):
            t_rows.append([
                Paragraph(f"AS{node['asn']}", cell_mono),
                Paragraph(node.get("name", ""), cell_style),
                Paragraph(node.get("info_type", ""), cell_style),
            ])
        t_table = Table(t_rows, colWidths=[0.9 * inch, 3.5 * inch, 1.3 * inch])
        t_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), zl_navy),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("BACKGROUND", (0, 1), (-1, -1), light_blue),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(Spacer(1, 4))
        elements.append(t_table)

    # Footer note
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        "Data sourced from PeeringDB and RIPEstat. "
        "Direct peer verification uses PeeringDB facility and IX co-location data. "
        "Peer classification is based on AS-path frequency analysis "
        "of Zenlayer prefix announcements.",
        ParagraphStyle("Footer", parent=styles["Normal"],
                        fontSize=8, textColor=colors.HexColor("#94a3b8")),
    ))

    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()


@app.get("/api/routing-flow/pdf")
async def routing_flow_pdf(location: str, location_type: str = "city"):
    """Generate and return a PDF export of the routing flow for a location."""
    try:
        tree_response = await get_routing_flow(
            location=location, location_type=location_type
        )
        if isinstance(tree_response, dict) and "error" in tree_response:
            raise HTTPException(status_code=404, detail=tree_response["error"])

        pdf_bytes = _build_routing_pdf(tree_response, location)
        safe_name = location.replace(" ", "_").replace("/", "-")

        import io
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="Zenlayer_Routing_{safe_name}.pdf"'
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API] PDF export error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
