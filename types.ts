
export interface PeeringDBNetwork {
  id: number;
  asn: number;
  name: string;
  info_type: string;
  policy_general: string;
  website: string;
  traffic_range?: string;
}

export interface PeeringDBFacility {
  id: number;
  name: string;
  city: string;
  country: string;
  address1: string;
  metro?: string; // Derived from mapping
}

export interface PeeringDBNetFac {
  id: number;
  net_id: number;
  fac_id: number;
  local_asn: number;
  name: string;
}

export interface ZenlayerLocation {
  city: string;
  facility: PeeringDBFacility;
}

export interface PeerDiscoveryResult {
  asn: number;
  name: string;
  type: 'Provider' | 'Peer';
  infoType: string;
  policy: string;
  trafficRange?: string;
}
