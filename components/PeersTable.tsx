
import React, { useState, useMemo } from 'react';
import { PeerDiscoveryResult } from '../types';
import { Icons } from '../constants';

interface PeersTableProps {
  peers: PeerDiscoveryResult[];
  loading: boolean;
  facilityName: string | null;
  viewMode: 'upstream' | 'peers' | 'all';
  isMarketScope: boolean;
}

const PeersTable: React.FC<PeersTableProps> = ({ peers, loading, facilityName, viewMode, isMarketScope }) => {
  const [searchQuery, setSearchQuery] = useState('');

  const filteredData = useMemo(() => {
    return peers.filter(p => 
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.asn.toString().includes(searchQuery)
    );
  }, [peers, searchQuery]);

  const tableTitle = {
    upstream: isMarketScope ? 'Market-wide Upstream Presence' : 'Site Upstream Providers',
    peers: 'Content & Peer Identification',
    all: 'Complete Network Footprint'
  }[viewMode];

  if (loading) {
    return (
      <div className="flex-1 bg-white rounded-3xl border border-slate-200 shadow-xl shadow-slate-200/50 flex flex-col items-center justify-center p-20">
        <div className="w-12 h-12 border-4 border-[#00205B] border-t-[#00A9E0] rounded-full animate-spin"></div>
        <p className="mt-4 text-slate-500 font-medium text-lg">Running Audit Analysis...</p>
      </div>
    );
  }

  if (!facilityName || facilityName === 'BGP Discovery') {
    return (
      <div className="flex-1 bg-white rounded-3xl border border-slate-200 shadow-xl shadow-slate-200/50 flex flex-col items-center justify-center p-20 text-center">
        <div className="w-16 h-16 bg-slate-100 rounded-3xl flex items-center justify-center mb-6">
          <Icons.Network />
        </div>
        <h3 className="text-xl font-black text-slate-700">Audit Ready</h3>
        <p className="max-w-xs text-sm text-slate-400 mt-2 font-medium">
          Select a Zenlayer data center or scan a full market market from the sidebar.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 bg-white rounded-3xl border border-slate-200 shadow-xl shadow-slate-200/50 overflow-hidden flex flex-col">
      <div className="px-8 py-6 bg-slate-50/50 border-b border-slate-100 flex flex-col md:flex-row justify-between items-center gap-6">
        <div className="flex flex-col">
          <h2 className="text-lg font-black text-[#00205B]">{tableTitle}</h2>
          <p className="text-xs text-slate-400 font-medium">Found {peers.length} unique networks</p>
        </div>
        
        <div className="relative w-full md:w-80">
          <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none text-slate-300">
            <Icons.Search />
          </div>
          <input
            type="text"
            placeholder="Filter by ASN or Provider..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-12 pr-4 py-3 bg-white border border-slate-200 rounded-2xl text-sm focus:ring-4 focus:ring-[#00A9E0]/10 focus:border-[#00A9E0] outline-none transition-all shadow-inner"
          />
        </div>
      </div>

      <div className="flex-1 overflow-auto custom-scrollbar">
        <table className="w-full text-left">
          <thead className="sticky top-0 bg-white/95 backdrop-blur z-10">
            <tr className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] border-b border-slate-100">
              <th className="px-8 py-5">ASN</th>
              <th className="px-8 py-5">Network Name</th>
              <th className="px-8 py-5">Info Type</th>
              {viewMode !== 'upstream' && <th className="px-8 py-5">Traffic Level</th>}
              <th className="px-8 py-5">Peering Policy</th>
              <th className="px-8 py-5 text-right">DB</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {filteredData.length > 0 ? (
              filteredData.map((peer) => (
                <tr key={peer.asn} className="hover:bg-slate-50/50 transition-all group">
                  <td className="px-8 py-5">
                    <span className="font-mono font-black text-[#00A9E0] bg-blue-50 px-2 py-1 rounded-lg">AS{peer.asn}</span>
                  </td>
                  <td className="px-8 py-5">
                    <div className="font-black text-[#00205B] group-hover:text-[#00A9E0] transition-colors">{peer.name}</div>
                  </td>
                  <td className="px-8 py-5">
                    <span className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-wider ${
                      peer.infoType === 'NSP' ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'
                    }`}>
                      {peer.infoType}
                    </span>
                  </td>
                  {viewMode !== 'upstream' && (
                    <td className="px-8 py-5 font-bold text-xs text-slate-600">
                      {peer.trafficRange || 'Unknown'}
                    </td>
                  )}
                  <td className="px-8 py-5 text-xs text-slate-500 font-medium leading-relaxed max-w-xs">
                    {peer.policy}
                  </td>
                  <td className="px-8 py-5 text-right">
                    <a
                      href={`https://www.peeringdb.com/asn/${peer.asn}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-slate-300 hover:text-[#00A9E0] transition-all"
                    >
                      <Icons.ExternalLink />
                    </a>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={viewMode === 'upstream' ? 5 : 6} className="px-8 py-20 text-center text-slate-400 italic font-medium">
                  No networks matching your filters found in this selection.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default PeersTable;
