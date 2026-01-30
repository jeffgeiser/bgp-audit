
import React from 'react';
import { Icons } from '../constants';
import { PeeringDBFacility } from '../types';

interface SidebarProps {
  metros: string[];
  cities: string[];
  facilities: PeeringDBFacility[];
  selectedLocation: { name: string; type: 'city' | 'metro' };
  selectedFacId: number | null;
  isMarketScope: boolean;
  currentView: 'dashboard' | 'settings';
  onLocationChange: (name: string, type: 'city' | 'metro') => void;
  onFacChange: (facId: number) => void;
  onScanMarket: () => void;
  onNavigate: (view: 'dashboard' | 'settings') => void;
  loading: boolean;
}

const Sidebar: React.FC<SidebarProps> = ({
  metros,
  cities,
  facilities,
  selectedLocation,
  selectedFacId,
  isMarketScope,
  currentView,
  onLocationChange,
  onFacChange,
  onScanMarket,
  onNavigate,
  loading,
}) => {
  // Determine which facilities to show based on selected location
  const filteredFacilities = facilities.filter(f => {
    if (!selectedLocation.name) return false;
    if (selectedLocation.type === 'city') return f.city === selectedLocation.name;
    // For metro, we need to check if the facility is in one of the cities mapped to this metro
    // But since initialize_footprint already tagged facilities with .metro, we can use that:
    return f.metro === selectedLocation.name;
  });

  return (
    <aside className="w-80 bg-[#00205B] border-r border-white/10 h-screen sticky top-0 flex flex-col text-white shadow-2xl z-30">
      <div 
        className="p-8 border-b border-white/10 flex items-center space-x-3 cursor-pointer hover:bg-white/5 transition-colors"
        onClick={() => onNavigate('dashboard')}
      >
        <div className="p-2 bg-[#00A9E0] rounded-lg shadow-lg">
          <Icons.Network />
        </div>
        <h1 className="text-xl font-bold tracking-tight uppercase">BGP Audit</h1>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-10 custom-scrollbar">
        {currentView === 'dashboard' ? (
          <>
            {/* Unified Location Selection */}
            <div className="space-y-4">
              <label className="text-[10px] font-black text-white/40 uppercase tracking-[0.2em] flex items-center gap-2">
                <Icons.Globe />
                Global Footprint
              </label>
              <select
                value={selectedLocation.name ? `${selectedLocation.type}:${selectedLocation.name}` : ""}
                onChange={(e) => {
                  const [type, name] = e.target.value.split(':');
                  if (!e.target.value) {
                    onLocationChange("", "city");
                  } else {
                    onLocationChange(name, type as 'city' | 'metro');
                  }
                }}
                disabled={loading}
                className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-sm focus:ring-2 focus:ring-[#00A9E0] outline-none text-white transition-all hover:bg-white/10 disabled:opacity-50"
              >
                <option value="" className="text-slate-900">Select Market scope...</option>
                
                {metros.length > 0 && (
                  <optgroup label="Aggregated Metro Areas" className="text-slate-500 bg-slate-100 font-black text-[10px] uppercase py-2">
                    {metros.map(m => (
                      <option key={`metro:${m}`} value={`metro:${m}`} className="text-slate-900 font-bold bg-white">
                        {m} (Metro)
                      </option>
                    ))}
                  </optgroup>
                )}

                <optgroup label="Individual Cities" className="text-slate-500 bg-slate-100 font-black text-[10px] uppercase py-2">
                  {cities.map(c => (
                    <option key={`city:${c}`} value={`city:${c}`} className="text-slate-900 bg-white">
                      {c}
                    </option>
                  ))}
                </optgroup>
              </select>
            </div>

            {/* Audit Scope */}
            <div className="space-y-4">
              <label className="text-[10px] font-black text-white/40 uppercase tracking-[0.2em] flex items-center gap-2">
                <Icons.Filter />
                Audit Scope
              </label>
              <div className="space-y-2 max-h-[500px] overflow-y-auto pr-2 custom-scrollbar">
                {!selectedLocation.name ? (
                  <div className="text-center py-12 opacity-20 italic text-xs font-medium bg-white/5 rounded-2xl border border-dashed border-white/10">
                    Select a market or city above<br/>to begin discovery
                  </div>
                ) : (
                  <>
                    <button
                      onClick={onScanMarket}
                      disabled={loading}
                      className={`w-full flex items-center justify-between px-4 py-4 rounded-xl text-xs transition-all border ${
                        isMarketScope 
                          ? 'bg-[#00A9E0] border-[#00A9E0] text-white shadow-xl font-bold' 
                          : 'bg-white/10 border-white/5 text-white/80 hover:bg-white/20'
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <Icons.Search />
                        <span>Scan Entire {selectedLocation.name}</span>
                      </div>
                      {isMarketScope && <div className="w-2 h-2 bg-white rounded-full animate-pulse"></div>}
                    </button>

                    <div className="h-px bg-white/10 my-4"></div>

                    {filteredFacilities.length > 0 ? (
                      filteredFacilities.sort((a,b) => a.name.localeCompare(b.name)).map(fac => (
                        <button
                          key={fac.id}
                          onClick={() => onFacChange(fac.id)}
                          disabled={loading}
                          className={`w-full text-left px-4 py-3 rounded-xl text-sm transition-all border border-transparent ${
                            selectedFacId === fac.id && !isMarketScope
                              ? 'bg-white text-[#00205B] font-bold shadow-lg scale-[1.02]'
                              : 'hover:bg-white/5 text-white/60'
                          }`}
                        >
                          <div className="truncate">{fac.name}</div>
                          <div className={`text-[10px] mt-0.5 opacity-50`}>
                            {fac.city}, {fac.country}
                          </div>
                        </button>
                      ))
                    ) : (
                       <div className="text-center py-6 opacity-40 text-[10px] uppercase tracking-widest font-bold">No DCs mapped in this view</div>
                    )}
                  </>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="space-y-6">
             <button
                onClick={() => onNavigate('dashboard')}
                className="w-full flex items-center gap-3 px-4 py-3 bg-white/10 hover:bg-white/20 rounded-xl text-sm transition-all border border-white/10"
              >
                <Icons.Globe />
                <span>Return to Dashboard</span>
              </button>
              <div className="p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl">
                 <h4 className="text-[10px] font-black uppercase tracking-widest text-[#00A9E0] mb-2">Editor Mode</h4>
                 <p className="text-xs text-white/60 leading-relaxed font-medium">Map individual PeeringDB cities to a custom Metro Label to enable aggregated market discovery.</p>
              </div>
          </div>
        )}
      </div>

      <div className="p-6 bg-black/10 border-t border-white/5 flex flex-col gap-4">
        <button 
          onClick={() => onNavigate(currentView === 'settings' ? 'dashboard' : 'settings')}
          className={`w-full flex items-center justify-center space-x-2 py-2.5 rounded-xl border transition-all text-[10px] font-black uppercase tracking-widest ${
            currentView === 'settings' 
            ? 'bg-[#00A9E0] border-[#00A9E0] text-white shadow-lg' 
            : 'bg-white/5 hover:bg-white/10 border-white/10 text-white'
          }`}
        >
          <Icons.Settings />
          <span>{currentView === 'settings' ? 'Close Settings' : 'Audit Settings'}</span>
        </button>
        <div className="text-[9px] text-white/20 text-center uppercase tracking-[0.3em] font-bold">
          Zenlayer Net Infrastructure
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
