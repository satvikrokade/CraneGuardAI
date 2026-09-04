import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Camera, Bell, ShieldCheck, Map, Trash2, Plus, Loader2, Check, Video } from 'lucide-react';
import { useWebSocketData } from '../context/WebSocketContext';

import { API_URL } from '../config';

export default function Settings() {
    const { data: wsData, status, cameraId, setCameraId } = useWebSocketData();
  const [config, setConfig] = useState({
    telegramToken: '',
    telegramChatId: '',
    alertCooldown: 30,
    ppeDetection: true
  });

  const [zones, setZones] = useState([]);
    const [cameras, setCameras] = useState([]);
    const [loadingZones, setLoadingZones] = useState(true);
    const [loadingCameras, setLoadingCameras] = useState(true);
    const [saveState, setSaveState] = useState('idle');
  const [isDrawingMode, setIsDrawingMode] = useState(false);
  const [newZoneName, setNewZoneName] = useState('');
    const [newCamName, setNewCamName] = useState('');
    const [newCamSource, setNewCamSource] = useState('');
    const [selectedZoneId, setSelectedZoneId] = useState(null);
    const [draftRect, setDraftRect] = useState(null);
    const [isDragging, setIsDragging] = useState(false);
    const [canvasSize, setCanvasSize] = useState({ width: 1280, height: 720 });

    const canvasRef = useRef(null);
    const frameImageRef = useRef(null);
    const dragStartRef = useRef(null);

    const frameSrc = wsData?.frame ? `data:image/jpeg;base64,${wsData.frame}` : null;

  useEffect(() => {
        const load = async () => {
             const zRes = await fetch(`${API_URL}/zones`);
             const cRes = await fetch(`${API_URL}/cameras`);
             const zData = await zRes.json();
             const cData = await cRes.json();
             if (Array.isArray(zData)) setZones(zData);
             if (Array.isArray(cData)) setCameras(cData);
             setLoadingZones(false);
             setLoadingCameras(false);
        };

        load();
  }, []);

    const drawZonePolygon = (ctx, zone, highlighted = false) => {
        const points = zone.polygon || [];
        if (points.length < 3) return;

        ctx.beginPath();
        points.forEach((p, i) => {
            if (i === 0) ctx.moveTo(p[0], p[1]);
            else ctx.lineTo(p[0], p[1]);
        });
        ctx.closePath();

        const color = zone.active === false ? '#6b7280' : highlighted ? '#14b8a6' : '#22c55e';
        ctx.strokeStyle = color;
        ctx.lineWidth = highlighted ? 4 : 2;
        ctx.fillStyle = zone.active === false ? 'rgba(107,114,128,0.15)' : 'rgba(34,197,94,0.16)';
        ctx.fill();
        ctx.stroke();

        const anchor = points[0];
        ctx.fillStyle = color;
        ctx.font = 'bold 22px monospace';
        ctx.fillText(zone.name || zone.id, anchor[0] + 6, anchor[1] - 8);
    };

    const redrawCanvas = useCallback(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const width = canvas.width;
        const height = canvas.height;

        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, width, height);

        if (frameImageRef.current) {
            ctx.drawImage(frameImageRef.current, 0, 0, width, height);
        } else {
            ctx.fillStyle = '#05070b';
            ctx.fillRect(0, 0, width, height);
            ctx.fillStyle = '#64748b';
            ctx.font = '16px monospace';
            ctx.fillText('Waiting for camera frame...', 40, 50);
        }

        zones.forEach((zone) => drawZonePolygon(ctx, zone, zone.id === selectedZoneId));

        if (draftRect) {
            const { x1, y1, x2, y2 } = draftRect;
            const minX = Math.min(x1, x2);
            const minY = Math.min(y1, y2);
            const w = Math.abs(x2 - x1);
            const h = Math.abs(y2 - y1);

            ctx.setLineDash([8, 6]);
            ctx.strokeStyle = '#facc15';
            ctx.lineWidth = 2;
            ctx.strokeRect(minX, minY, w, h);
            ctx.setLineDash([]);
            ctx.fillStyle = 'rgba(250, 204, 21, 0.18)';
            ctx.fillRect(minX, minY, w, h);
        }
    }, [draftRect, selectedZoneId, zones]);

    useEffect(() => {
        redrawCanvas();
    }, [redrawCanvas]);

    useEffect(() => {
        if (!frameSrc) return;
        const img = new Image();
        img.onload = () => {
            frameImageRef.current = img;
            setCanvasSize({ width: img.width || 1280, height: img.height || 720 });
            redrawCanvas();
        };
        img.src = frameSrc;
    }, [frameSrc, redrawCanvas]);

    const getCanvasPoint = (event) => {
        const canvas = canvasRef.current;
        const rect = canvas.getBoundingClientRect();
        const x = Math.max(0, Math.min(canvas.width, Math.round((event.clientX - rect.left) * (canvas.width / rect.width))));
        const y = Math.max(0, Math.min(canvas.height, Math.round((event.clientY - rect.top) * (canvas.height / rect.height))));
        return { x, y };
    };

    const handleCanvasMouseDown = (event) => {
        if (!isDrawingMode) return;
        const { x, y } = getCanvasPoint(event);
        dragStartRef.current = { x, y };
        setDraftRect({ x1: x, y1: y, x2: x, y2: y });
        setIsDragging(true);
    };

    const handleCanvasMouseMove = (event) => {
        if (!isDrawingMode || !isDragging || !dragStartRef.current) return;
        const { x, y } = getCanvasPoint(event);
        const start = dragStartRef.current;
        setDraftRect({ x1: start.x, y1: start.y, x2: x, y2: y });
    };

    const handleCanvasMouseUp = () => {
        if (!isDrawingMode) return;
        setIsDragging(false);
    };

    const addDraftZone = () => {
        if (!draftRect) return;
        const minX = Math.min(draftRect.x1, draftRect.x2);
        const minY = Math.min(draftRect.y1, draftRect.y2);
        const maxX = Math.max(draftRect.x1, draftRect.x2);
        const maxY = Math.max(draftRect.y1, draftRect.y2);

        if ((maxX - minX) < 20 || (maxY - minY) < 20) return;

        const polygon = [[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY]];
        const cleanName = newZoneName.trim();
        const zoneLabel = cleanName || `Zone ${zones.length + 1}`;
        const zoneId = cleanName
            ? cleanName.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '') || `ZONE_${Date.now()}`
            : `ZONE_${Date.now()}`;

        const nextZone = {
            id: zoneId,
            name: zoneLabel,
            polygon,
            active: true,
            camera_source: cameraId || '0'
        };

        const nextZones = [...zones, nextZone];
        setZones(nextZones);
        syncZones(nextZones);
        setSelectedZoneId(zoneId);
        setNewZoneName('');
        setDraftRect(null);
        setIsDrawingMode(false);
    };

    const addCamera = async () => {
        if (!newCamName || !newCamSource) return;
        const nextCam = {
            id: `CAM_${Date.now()}`,
            name: newCamName,
            source: newCamSource
        };
        const nextCameras = [...cameras, nextCam];
        setCameras(nextCameras);
        await fetch('/api/cameras', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(nextCameras)
        });
        setNewCamName('');
        setNewCamSource('');
    };

    const removeCamera = async (id) => {
        const nextCameras = cameras.filter(c => c.id !== id);
        setCameras(nextCameras);
        await fetch(`${API_URL}/cameras`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(nextCameras)
        });
    };

    const clearDraft = () => {
        setDraftRect(null);
        dragStartRef.current = null;
    };

    const syncZones = async (nextZones) => {
        setSaveState('saving');
        try {
            await fetch(`${API_URL}/zones`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(nextZones)
            });
            setSaveState('saved');
            setTimeout(() => setSaveState('idle'), 1500);
        } catch {
            setSaveState('idle');
        }
    };

  const handleSave = async () => {
            await syncZones(zones);
  };

  return (
    <div className="p-4 md:p-8 flex flex-col gap-8 h-full bg-background overflow-y-auto">
      <div>
        <h2 className="text-4xl font-display font-bold text-white tracking-widest leading-none">SYSTEM SETTINGS</h2>
        <p className="text-xs font-mono text-slate-500 mt-2">CONFIGURE HARDWARE AND ALERT PARAMETERS</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 pb-20 md:pb-0">
        <div className="space-y-8">
            {/* Camera Management */}
            <div className="bg-card border border-white/5 rounded-2xl p-6 space-y-4">
                <div className="flex items-center justify-between text-white border-b border-white/5 pb-4 mb-4">
                    <div className="flex items-center gap-3">
                        <Camera size={20} className="text-teal-500" />
                        <h3 className="font-display font-bold">MONITORING CAMERAS</h3>
                    </div>
                </div>
                
                <div className="space-y-3">
                    {cameras.map(cam => (
                        <div key={cam.id} className={`p-3 rounded-xl border flex items-center justify-between group transition-all ${cameraId === cam.id ? 'bg-teal-500/10 border-teal-500/50' : 'bg-white/5 border-white/5 hover:bg-white/10'}`}>
                            <div className="flex flex-col cursor-pointer" onClick={() => setCameraId(cam.id)}>
                                <span className="text-xs font-bold text-white uppercase">{cam.name}</span>
                                <span className="text-[9px] font-mono text-slate-500 truncate max-w-[150px]">{cam.source}</span>
                            </div>
                            <div className="flex items-center gap-2">
                                <button 
                                    onClick={() => setCameraId(cam.id)}
                                    className={`px-2 py-1 rounded text-[8px] font-mono font-bold border transition-all ${cameraId === cam.id ? 'bg-teal-500 text-background' : 'bg-white/5 border-white/10 text-slate-400'}`}
                                >
                                    {cameraId === cam.id ? 'ACTIVE' : 'SELECT'}
                                </button>
                                <button 
                                    onClick={() => removeCamera(cam.id)}
                                    className="p-1.5 text-slate-600 hover:text-red-500 transition-colors"
                                >
                                    <Trash2 size={12} />
                                </button>
                            </div>
                        </div>
                    ))}
                    
                    <div className="pt-4 border-t border-white/5 mt-4">
                        <label className="text-[10px] font-mono text-slate-600 uppercase block mb-3 underline decoration-teal-500/30">Provision New Hardware</label>
                        <div className="flex flex-col gap-2">
                            <input 
                                type="text"
                                placeholder="Camera Name (e.g. South Gate)"
                                className="w-full bg-background border border-white/10 rounded px-3 py-2 text-xs text-white focus:border-teal-500 outline-none"
                                value={newCamName}
                                onChange={e => setNewCamName(e.target.value)}
                            />
                            <div className="flex gap-2">
                                <input 
                                    type="text"
                                    placeholder="RTSP / IP Webcam URL / Index"
                                    className="flex-1 bg-background border border-white/10 rounded px-3 py-2 text-xs text-white focus:border-teal-500 outline-none font-mono"
                                    value={newCamSource}
                                    onChange={e => setNewCamSource(e.target.value)}
                                />
                                <button 
                                    onClick={addCamera}
                                    className="bg-teal-500 text-background px-3 rounded hover:bg-teal-400 transition-colors"
                                >
                                    <Plus size={16} />
                                </button>
                            </div>
                            <div className="flex gap-2 mt-2">
                                <button 
                                    onClick={() => { setNewCamName("AI Simulation"); setNewCamSource("SIMULATION"); }}
                                    className="px-2 py-1 bg-white/5 hover:bg-teal-500/20 text-teal-400 border border-teal-500/30 rounded text-[9px] font-mono"
                                >
                                    + Presets: Synthetic Simulation
                                </button>
                                <button 
                                    onClick={() => { setNewCamName("Laptop Webcam"); setNewCamSource("0"); }}
                                    className="px-2 py-1 bg-white/5 hover:bg-white/10 text-slate-400 border border-white/10 rounded text-[9px] font-mono"
                                >
                                    + Presets: Local Cam 0
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Notifications */}
            <div className="bg-card border border-white/5 rounded-2xl p-6 space-y-4">
                <div className="flex items-center gap-3 text-white border-b border-white/5 pb-4 mb-4">
                    <Bell size={20} className="text-amber-500" />
                    <h3 className="font-display font-bold">ALERT ENGINE</h3>
                </div>
                <div className="space-y-4">
                    <div>
                        <label className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Telegram Bot Token</label>
                        <input 
                            type="password" 
                            className="w-full bg-background border border-white/10 rounded-lg px-4 py-2 text-sm font-mono text-white focus:outline-none focus:border-teal-500"
                            defaultValue="************"
                        />
                    </div>
                    <div>
                        <label className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Chat ID</label>
                        <input 
                            type="text" 
                            className="w-full bg-background border border-white/10 rounded-lg px-4 py-2 text-sm font-mono text-white focus:outline-none focus:border-teal-500"
                            defaultValue="8271928"
                        />
                    </div>
                    <div>
                        <label className="text-[10px] font-mono text-slate-500 uppercase block mb-1 flex justify-between">
                            <span>Cooldown Period (seconds)</span>
                            <span className="text-teal-500">{config.alertCooldown}s</span>
                        </label>
                        <input 
                            type="range" min="10" max="120" 
                            className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer accent-teal-500"
                            value={config.alertCooldown}
                            onChange={(e) => setConfig({...config, alertCooldown: e.target.value})}
                        />
                    </div>
                </div>
            </div>
        </div>

        <div className="space-y-8">
            {/* Zone Mapping */}
            <div className="bg-card border border-white/5 rounded-2xl p-6 flex flex-col gap-4 min-h-[500px]">
                <div className="flex items-center justify-between text-white border-b border-white/5 pb-4 mb-4">
                    <div className="flex items-center gap-3">
                        <Map size={20} className="text-pink-500" />
                        <h3 className="font-display font-bold">SAFETY ZONES</h3>
                    </div>
                    <button
                        onClick={() => {
                          setIsDrawingMode((prev) => !prev);
                          clearDraft();
                        }}
                        className="bg-teal-500 text-background px-3 py-1 rounded hover:bg-teal-400 transition-colors text-[10px] font-mono tracking-wider"
                    >
                        <span className="inline-flex items-center gap-1"><Plus size={14} /> ADD CUSTOM BOX</span>
                    </button>
                </div>
                
                <div className="flex-1 space-y-4">
                                        {loadingZones ? (
                                            <div className="flex items-center gap-2 text-xs font-mono text-slate-400">
                                                <Loader2 size={14} className="animate-spin" />
                                                Loading zone definitions...
                                            </div>
                                        ) : zones.map((zone) => (
                                                <div
                                                    key={zone.id}
                                                    className={`bg-white/5 border p-4 rounded-xl flex items-center justify-between group cursor-pointer ${selectedZoneId === zone.id ? 'border-teal-500/70' : 'border-white/5'}`}
                                                    onClick={() => setSelectedZoneId(zone.id)}
                                                >
                                                     <div className="flex flex-col">
                                 <span className="font-display font-bold text-white uppercase">{zone.name}</span>
                                 <div className="flex items-center gap-2">
                                     <span className="text-[8px] font-mono text-slate-600 uppercase tracking-widest">{(zone.polygon || []).length} POL POINTS</span>
                                     <span className="text-[8px] font-mono text-teal-500/50 uppercase tracking-widest flex items-center gap-1">
                                         <Video size={8} /> {cameras.find(c => c.id === zone.camera_source)?.name || 'UNKNOWN CAM'}
                                     </span>
                                 </div>
                             </div>
                                                        <div className="flex items-center gap-2">
                                                                <button
                                                                    onClick={(e) => {
                                                                        e.stopPropagation();
                                                                        const nextZones = zones.map((z) => z.id === zone.id ? { ...z, active: !z.active } : z);
                                                                        setZones(nextZones);
                                                                        syncZones(nextZones);
                                                                    }}
                                                                    className={`px-2 py-1 text-[9px] rounded border ${zone.active !== false ? 'bg-green-500/10 border-green-500/30 text-green-400' : 'bg-slate-500/10 border-slate-500/30 text-slate-400'}`}
                                                                >
                                                                    {zone.active !== false ? 'ON' : 'OFF'}
                                                                </button>
                                                                <button
                                                                    onClick={(e) => {
                                                                        e.stopPropagation();
                                                                        const nextZones = zones.filter((z) => z.id !== zone.id);
                                                                        setZones(nextZones);
                                                                        syncZones(nextZones);
                                                                        if (selectedZoneId === zone.id) setSelectedZoneId(null);
                                                                    }}
                                                                    className="p-2 text-slate-500 hover:text-red-500 transition-colors"
                                                                    title="Delete zone"
                                                                >
                                                                    <Trash2 size={14} />
                                                                </button>
                            </div>
                        </div>
                                        ))}
                    
                    {isDrawingMode && (
                        <div className="space-y-2">
                            <div className="flex justify-between items-end">
                                                                <label className="text-[10px] font-mono text-slate-500 uppercase block">Draw New Area From Camera</label>
                                <input 
                                    type="text" 
                                    placeholder="Zone Name"
                                    value={newZoneName}
                                    onChange={(e) => setNewZoneName(e.target.value)}
                                    className="bg-black/50 border border-white/10 rounded px-2 py-1 text-xs text-white"
                                />
                            </div>
                            <div className="relative aspect-video bg-black rounded-xl border border-white/10 overflow-hidden cursor-crosshair">
                                <canvas 
                                  ref={canvasRef}
                                                                    width={canvasSize.width}
                                                                    height={canvasSize.height}
                                  className="w-full h-full"
                                  onMouseDown={handleCanvasMouseDown}
                                  onMouseMove={handleCanvasMouseMove}
                                  onMouseUp={handleCanvasMouseUp}
                                  onMouseLeave={handleCanvasMouseUp}
                                />
                                <div className="absolute left-2 top-2 text-[10px] font-mono px-2 py-1 rounded bg-black/50 border border-white/10 text-slate-300">
                                  {status === 'connected' ? 'LIVE CAMERA FRAME' : 'NO LIVE FEED'}
                                </div>
                            <div className="absolute top-2 right-2 flex gap-2">
                                <button 
                                    onClick={addDraftZone}
                                    className="bg-teal-500 text-background text-[8px] font-bold px-3 py-1 rounded shadow-lg"
                                >
                                    ADD ZONE
                                </button>
                                <button 
                                    onClick={clearDraft}
                                    className="bg-white/10 text-white text-[8px] font-bold px-3 py-1 rounded"
                                >
                                    CLEAR
                                </button>
                            </div>
                            <div className="absolute bottom-2 left-2 text-[9px] font-mono px-2 py-1 rounded bg-black/50 border border-white/10 text-slate-300">
                              Drag on the frame to draw a new green box area
                            </div>
                        </div>
                    </div>
                    )}
                </div>

                <div className="mt-4 p-4 bg-teal-500/10 border border-teal-500/20 rounded-xl">
                    <div className="flex items-center gap-3 text-teal-500 mb-2">
                        <ShieldCheck size={16} />
                        <span className="text-xs font-bold uppercase tracking-wider">AI Guard Enabled</span>
                    </div>
                    <p className="text-[10px] font-mono text-slate-400">YOLOv8 + DeepSORT Active. Zone crossing triggers immediate Telegram alert.</p>
                </div>
            </div>
        </div>
      </div>

      <div className="flex flex-col md:flex-row justify-end gap-3 md:gap-4 mt-12 pb-32 md:pb-8 border-t border-white/5 pt-8">
         <button className="flex-1 md:flex-none px-8 py-3 bg-white/5 border border-white/10 rounded-lg text-slate-400 font-display font-bold hover:bg-white/10 transition-colors uppercase tracking-widest text-sm">DISCARD</button>
            <button onClick={handleSave} className="flex-1 md:flex-none px-8 py-3 bg-teal-500 rounded-lg text-background font-display font-bold hover:bg-teal-400 transition-all active:scale-95 flex items-center justify-center gap-2 uppercase tracking-widest text-sm shadow-lg shadow-teal-500/20">
                {saveState === 'saving' && <Loader2 size={16} className="animate-spin" />}
                {saveState === 'saved' && <Check size={16} />}
                {saveState === 'saving' ? 'SAVING...' : saveState === 'saved' ? 'SAVED' : 'SAVE CONFIGURATION'}
            </button>
      </div>
    </div>
  );
}
