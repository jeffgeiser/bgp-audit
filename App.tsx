import React, { useState, useEffect, useMemo } from 'react';
import { peeringDbService } from './services/peeringDbService';
import { PeeringDBFacility, PeerDiscoveryResult } from './types';
import { Icons } from './constants';
import Sidebar from './components/Sidebar';
import PeersTable from './components/PeersTable';

const SettingsView: React.FC<{ 
  onClose: () => void; 
  onSave: () => void;
}> = ({ onClose, onSave }) => {
  const [config, setConfig] = useState<{ ASNS: number[], METRO_MAP: Record<string, string> }>({ ASNS: [], METRO_MAP: {} });
  const [discoveredCities, setDiscoveredCities] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [view, setView] = useState<'visual' | 'advanced'>('visual');

  const fetchConfig = async () => {
    try {
      setLoading(true);
      const res = await peeringDbService.getSettings();
      setConfig(res.config);
      setDiscoveredCities(res.discovered_cities || []);
      setError(null);
    } catch (err: any) {
      setError(`Failed to load configuration: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConfig();
  }, []);

  const handleMetroChange = (city: string, metro: string) => {
    const newMap = { ...config.METRO_MAP };
    if (!metro) {
      delete newMap[city];
    } else {
      newMap[city] = metro;
    }
    setConfig({ ...config, METRO_MAP: newMap });
  };

  const handleASNChange = (value: string) => {
    const asns = value.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
    setConfig({ ...config, ASNS: asns });
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      await peeringDbService.updateSettings(config);
      setSuccess(true);
      onSave();
      setTimeout(() => setSuccess(false), 3000);
    } catch (err: any) {
      setError(`Failed to save: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full space-y-4">
        <div className="w-10 h-10 border-4 border-[#00205B] border-t-[#00A9E0] rounded-full animate-spin"></div>
        <p className="text-slate-500 font-bold uppercase tracking-widest text-xs">Syncing Settings...</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full gap-8">
      <header className="flex justify-between items-end">
        <div>
          <nav className="flex text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 gap-2 items-center">
            <span className="px-2 py-0.5 rounded bg-indigo-50 text-indigo-600">ADMINISTRATION</span>
            <span>/</span>
            <span className="text-[#00A9E0]">Infrastructure Management</span>
          </nav>
          <h1 className="text-4xl font-black text-[#00205B] tracking-tight leading-none">Settings Hub</h1>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex bg-slate-100 p-1 rounded-xl mr-4">
            <button onClick={() => setView('visual')} className={`px-4 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all ${view === 'visual' ? 'bg-white text-[#00205B] shadow-sm' : 'text-slate-400'}`}>Visual Editor</button>
            <button onClick={() => setView('advanced')} className={`px-4 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all ${view === 'advanced' ? 'bg-white text-[#00205B] shadow-sm' : 'text-slate-400'}`}>Advanced JSON</button>
          </div>
          <button 
            onClick={handleSave}
            disabled={saving}
            className="px-8 py-2.5 bg-[#00A9E0] hover:bg-[#008dbb] disabled:opacity-50 text-white rounded-xl text-[10px] font-black transition-all shadow-lg uppercase tracking-widest"
          >
            {saving ? 'Saving...' : 'Save Configuration'}
          </button>
        </div>
      </header>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-2xl flex justify-between items-center text-xs font-bold">
          <span>{error}</span>
          <button onClick={fetchConfig} className="text-red-900 underline">Retry Load</button>
        </div>
      )}

      {success && (
        <div className="bg-emerald-50 border border-emerald-200 text-emerald-700 p-4 rounded-2xl text-xs font-bold animate-pulse">
          Changes applied. PeeringDB footprint is being re-indexed.
        </div>
      )}

      <div className="flex-1 overflow-hidden flex flex-col gap-8">
        {view === 'visual' ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 h-full min-h-0">
            {/* ASN SECTION */}
            <div className="flex flex-col gap-6">
              <div className="bg-white rounded-3xl border border-slate-200 shadow-xl p-8 flex flex-col gap-6">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-indigo-50 text-indigo-600 rounded-lg"><Icons.Network /></div>
                  <div>
                    <h3 className="text-sm font-black text-[#00205B] uppercase tracking-widest">Network ASNs</h3>
                    <p className="text-[10px] text-slate-400 font-medium">Define which networks to audit</p>
                  </div>
                </div>
                <div className="space-y-2">
                  <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Active ASN List (comma separated)</label>
                  <input 
                    type="text"
                    value={config.ASNS.join(', ')}
                    onChange={(e) => handleASNChange(e.target.value)}
                    className="w-full bg-slate-50 border border-slate-100 rounded-xl px-4 py-3 font-mono text-sm focus:bg-white focus:ring-2 focus:ring-[#00A9E0] outline-none transition-all"
                  />
                  <p className="text-[9px] text-slate-400 leading-relaxed mt-2 italic font-medium">Default: 21859, 4229.</p>
                </div>
              </div>

              <div className="bg-amber-50 border border-amber-100 p-8 rounded-3xl flex flex-col gap-4">
                 <h4 className="text-xs font-black text-amber-900 uppercase tracking-widest">Metro Mapping Logic</h4>
                 <p className="text-xs text-amber-700 leading-relaxed font-medium">
                   Map individual PeeringDB cities (e.g., Ashburn) to aggregated "Metro Areas" (e.g., Washington DC (IAD)). Metros appear at the top of the sidebar filter and aggregate results across all mapped cities.
                 </p>
              </div>
            </div>

            {/* METRO MAP SECTION */}
            <div className="bg-white rounded-3xl border border-slate-200 shadow-xl overflow-hidden flex flex-col min-h-0">
              <div className="px-8 py-6 bg-slate-50/50 border-b border-slate-100 flex justify-between items-center">
                <div className="flex items-center gap-3">
                   <div className="p-2 bg-[#00A9E0]/10 text-[#00A9E0] rounded-lg"><Icons.Globe /></div>
                   <h3 className="text-sm font-black text-[#00205B] uppercase tracking-widest">Metro Mapping Studio</h3>
                </div>
                <span className="text-[10px] font-black text-slate-400 bg-white px-2 py-1 rounded-lg border border-slate-200">{discoveredCities.length} Footprint Cities</span>
              </div>
              <div className="flex-1 overflow-y-auto p-8 custom-scrollbar">
                <table className="w-full text-left">
                  <thead className="text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100">
                    <tr>
                      <th className="pb-4 pr-4">Discovered City</th>
                      <th className="pb-4">Audit Metro Area Label</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {discoveredCities.map(city => (
                      <tr key={city} className="group">
                        <td className="py-4 pr-4">
                          <span className="text-sm font-black text-[#00205B]">{city}</span>
                        </td>
                        <td className="py-4">
                          <input 
                            type="text"
                            placeholder="Map to Metro (e.g. Silicon Valley)..."
                            value={config.METRO_MAP[city] || ''}
                            onChange={(e) => handleMetroChange(city, e.target.value)}
                            className="w-full bg-slate-50 border border-transparent group-hover:border-slate-200 rounded-lg px-3 py-2 text-xs focus:bg-white focus:ring-2 focus:ring-[#00A9E0] outline-none transition-all placeholder:text-slate-300"
                          />
                        </td>
                      </tr>
                    ))}
                    {discoveredCities.length === 0 && (
                      <tr>
                        <td colSpan={2} className="py-20 text-center text-slate-400 text-xs italic font-medium">No cities discovered for current ASNs.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 bg-white rounded-3xl border border-slate-200 shadow-xl overflow-hidden flex flex-col">
            <div className="px-8 py-4 bg-slate-900 text-white flex justify-between items-center">
              <span className="text-[10px] font-black uppercase tracking-widest">config.json (Power User Editor)</span>
            </div>
            <textarea 
              value={JSON.stringify(config, null, 4)}
              onChange={(e) => {
                try {
                  const parsed = JSON.parse(e.target.value);
                  setConfig(parsed);
                } catch(err) {}
              }}
              spellCheck={false}
              className="flex-1 p-8 font-mono text-sm bg-slate-50 outline-none resize-none"
            />
          </div>
        )}
      </div>
    </div>
  );
};

const App: React.FC = () => {
  const [initLoading, setInitLoading] = useState(true);
  const [dataLoading, setDataLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentView, setCurrentView] = useState<'dashboard' | 'settings'>('dashboard');

  const [zenlayerFacs, setZenlayerFacs] = useState<PeeringDBFacility[]>([]);
  const [metros, setMetros] = useState<string[]>([]);
  const [cities, setCities] = useState<string[]>([]);
  const [networks, setNetworks] = useState<PeerDiscoveryResult[]>([]);

  // Selection state
  const [selectedLocation, setSelectedLocation] = useState<{ name: string; type: 'city' | 'metro' }>({ name: '', type: 'city' });
  const [selectedFacId, setSelectedFacId] = useState<number | null>(null);
  const [isMarketScope, setIsMarketScope] = useState<boolean>(false);
  const [viewMode, setViewMode] = useState<'upstream' | 'peers' | 'all'>('upstream');

  const refreshFootprint = async () => {
    try {
      const res = await peeringDbService.getSettings();
      const currentConfig = res.config;
      const currentMetroMap = currentConfig.METRO_MAP || {};
      const asns = currentConfig.ASNS || [];
      
      if (asns.length > 0) {
        const nets = await peeringDbService.getZenlayerNets(asns);
        const netIds = nets.map(n => n.id);
        const netFacs = await peeringDbService.getNetworkFacilities(netIds);
        const facIds = [...new Set(netFacs.map(nf => nf.fac_id))];
        const facDetails = await peeringDbService.getFacilities(facIds);
        
        const enrichedFacs = facDetails.map(f => ({
          ...f,
          metro: currentMetroMap[f.city] || undefined
        }));
        
        setZenlayerFacs(enrichedFacs);
        setCities([...new Set(enrichedFacs.map(f => f.city))]);
        setMetros([...new Set(enrichedFacs.filter(f => f.metro).map(f => f.metro as string))]);
      }
    } catch (err: any) {
      setError(`Engine synchronization failure: ${err.message}`);
    }
  };

  useEffect(() => {
    const initializeData = async () => {
      setInitLoading(true);
      await refreshFootprint();
      setInitLoading(false);
    };
    initializeData();
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      if (!selectedFacId && !isMarketScope) {
        setNetworks([]);
        return;
      }
      try {
        setDataLoading(true);
        // Fix: Removed redundant discovery call that used the non-existent 'city' property and lacked 'category'.
        // Execute discovery with standardized parameters.
        const finalResults = await peeringDbService.discover({
          facId: isMarketScope ? undefined : (selectedFacId || undefined),
          location: isMarketScope ? selectedLocation.name : undefined,
          locationType: selectedLocation.type,
          category: viewMode
        });

        setNetworks(finalResults);
      } catch (err: any) {
        console.error("Discovery error:", err);
      } finally {
        setDataLoading(false);
      }
    };
    fetchData();
  }, [selectedFacId, isMarketScope, viewMode, selectedLocation]);

  const activeTitle = useMemo(() => {
    if (isMarketScope) return `${selectedLocation.name} (${selectedLocation.type.toUpperCase()}) Aggregation`;
    return zenlayerFacs.find(f => f.id === selectedFacId)?.name || 'BGP Discovery';
  }, [zenlayerFacs, selectedFacId, isMarketScope, selectedLocation]);

  const viewModeLabel = {
    upstream: 'Upstream Providers',
    peers: 'Content & Peers',
    all: 'Global Networks'
  }[viewMode];

  if (initLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center space-y-4">
        <div className="w-16 h-16 border-4 border-[#00205B] border-t-[#00A9E0] rounded-full animate-spin"></div>
        <p className="text-[#00205B] font-bold text-lg animate-pulse uppercase tracking-[0.3em]">Zenlayer Auditor</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-[#F8FAFC]">
      <Sidebar
        metros={metros}
        cities={cities}
        facilities={zenlayerFacs}
        selectedLocation={selectedLocation}
        selectedFacId={selectedFacId}
        isMarketScope={isMarketScope}
        currentView={currentView}
        onLocationChange={(name, type) => {
          setSelectedLocation({ name, type });
          setSelectedFacId(null);
          setIsMarketScope(!!name);
          setCurrentView('dashboard');
        }}
        onFacChange={(id) => {
          setSelectedFacId(id);
          setIsMarketScope(false);
          setCurrentView('dashboard');
        }}
        onScanMarket={() => {
          setIsMarketScope(true);
          setSelectedFacId(null);
          setCurrentView('dashboard');
        }}
        onNavigate={setCurrentView}
        loading={dataLoading}
      />

      <main className="flex-1 p-10 flex flex-col gap-8 overflow-y-auto">
        {currentView === 'dashboard' ? (
          <>
            <header className="flex justify-between items-end">
              <div>
                <nav className="flex text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 gap-2 items-center">
                  <span className={`px-2 py-0.5 rounded ${isMarketScope ? 'bg-indigo-50 text-indigo-600' : 'bg-slate-100 text-slate-600'}`}>
                    {isMarketScope ? 'MARKET AGGREGATION' : 'SITE AUDIT'}
                  </span>
                  <span>/</span>
                  <span className="text-[#00A9E0]">{viewModeLabel}</span>
                </nav>
                <h1 className="text-4xl font-black text-[#00205B] tracking-tight leading-none">
                  {activeTitle}
                </h1>
              </div>
              
              <div className="flex items-center bg-white p-1.5 rounded-2xl border border-slate-200 shadow-sm">
                {(['upstream', 'peers', 'all'] as const).map((mode) => (
                  <button 
                    key={mode}
                    onClick={() => setViewMode(mode)}
                    className={`px-5 py-2.5 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all ${
                      viewMode === mode 
                        ? 'bg-[#00205B] text-white shadow-xl scale-[1.05]' 
                        : 'text-slate-500 hover:text-slate-800'
                    }`}
                  >
                    {mode === 'peers' ? 'Content & Peers' : mode === 'all' ? 'All Networks' : 'Upstream'}
                  </button>
                ))}
              </div>
            </header>

            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 px-6 py-4 rounded-2xl flex items-center justify-between">
                <span className="text-sm font-bold">{error}</span>
                <button onClick={() => { setError(null); refreshFootprint(); }} className="text-red-900 underline font-black">Retry Analysis</button>
              </div>
            )}

            <PeersTable 
              peers={networks} 
              loading={dataLoading} 
              facilityName={activeTitle}
              viewMode={viewMode}
              isMarketScope={isMarketScope}
            />
          </>
        ) : (
          <SettingsView 
            onClose={() => setCurrentView('dashboard')} 
            onSave={() => refreshFootprint()} 
          />
        )}

        <footer className="mt-auto pt-8 flex justify-between items-center text-[10px] font-black text-slate-400 uppercase tracking-[0.3em]">
          <div className="flex gap-4">
            <p>Source: PeeringDB API</p>
            <p>|</p>
            <p>ZENLAYER FOOTPRINT</p>
          </div>
          <p>&copy; Zenlayer Global Operations</p>
        </footer>
      </main>
    </div>
  );
};

export default App;