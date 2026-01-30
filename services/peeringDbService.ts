
import { PeeringDBNetwork, PeeringDBFacility, PeeringDBNetFac, PeerDiscoveryResult } from '../types';

const PEERINGDB_API_BASE = 'https://www.peeringdb.com/api';
const LOCAL_API_BASE = '/audit/api';

class PeeringDBCache {
  private cache: Map<string, { data: any; timestamp: number }> = new Map();
  private readonly TTL = 24 * 60 * 60 * 1000; 

  set(key: string, data: any) {
    this.cache.set(key, { data, timestamp: Date.now() });
  }

  get(key: string) {
    const entry = this.cache.get(key);
    if (!entry) return null;
    if (Date.now() - entry.timestamp > this.TTL) {
      this.cache.delete(key);
      return null;
    }
    return entry.data;
  }
}

const cache = new PeeringDBCache();

const fetchWithCache = async <T,>(url: string, options?: RequestInit): Promise<T> => {
  const cacheKey = `${options?.method || 'GET'}:${url}`;
  if (!options || options.method === 'GET') {
    const cached = cache.get(cacheKey);
    if (cached) return cached as T;
  }

  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      const statusText = response.statusText || `Status ${response.status}`;
      throw new Error(`API error: ${statusText} while fetching ${url}`);
    }
    const data = await response.json();
    const result = data.data !== undefined ? data.data as T : data as T;
    
    if (!options || options.method === 'GET') {
      cache.set(cacheKey, result);
    }
    return result;
  } catch (error) {
    if (error instanceof Error) {
      throw error;
    }
    throw new Error('An unknown network error occurred');
  }
};

export const peeringDbService = {
  getSettings: async (): Promise<{ config: { ASNS: number[], METRO_MAP: Record<string, string> }, discovered_cities: string[] }> => {
    return fetchWithCache(`${LOCAL_API_BASE}/settings`);
  },

  updateSettings: async (config: any): Promise<any> => {
    return fetchWithCache(`${LOCAL_API_BASE}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    });
  },

  getZenlayerNets: async (asns: number[]): Promise<PeeringDBNetwork[]> => {
    const asnStr = asns.join(',');
    return fetchWithCache<PeeringDBNetwork[]>(`${PEERINGDB_API_BASE}/net?asn__in=${asnStr}`);
  },

  getNetworkFacilities: async (netIds: number[]): Promise<PeeringDBNetFac[]> => {
    const ids = netIds.join(',');
    return fetchWithCache<PeeringDBNetFac[]>(`${PEERINGDB_API_BASE}/netfac?net_id__in=${ids}`);
  },

  getFacilities: async (facIds: number[]): Promise<PeeringDBFacility[]> => {
    const ids = facIds.join(',');
    return fetchWithCache<PeeringDBFacility[]>(`${PEERINGDB_API_BASE}/fac?id__in=${ids}`);
  },

  discover: async (params: { facId?: number, location?: string, locationType?: 'city' | 'metro', category: string }): Promise<PeerDiscoveryResult[]> => {
    const query = new URLSearchParams();
    if (params.facId) query.append('fac_id', params.facId.toString());
    if (params.location) query.append('location', params.location);
    if (params.locationType) query.append('location_type', params.locationType);
    query.append('category', params.category);

    const url = `${LOCAL_API_BASE}/discover?${query.toString()}`;
    const data = await fetchWithCache<any[]>(url);
    
    return data.map(net => ({
      asn: net.asn,
      name: net.name,
      infoType: net.info_type || 'N/A',
      type: (net.info_type === 'NSP' || net.info_type?.includes('Transit')) ? 'Provider' : 'Peer',
      policy: net.policy,
      trafficRange: net.traffic_range || 'Unknown'
    }));
  }
};
