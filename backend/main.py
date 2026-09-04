import cv2
import base64
import json
import asyncio
import os
import time
import platform
import numpy as np
from typing import Any, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from detector import CraneDetector
from zone_logic import check_zones, is_box_in_zone
from alert_engine import AlertEngine
from fatigue_detector import FatigueDetector
from acoustic_detector import AcousticDetector
from report_engine import generate_shift_report
from dotenv import load_dotenv

def make_serializable(obj):
    """Recursively convert numpy types to native Python types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_serializable(v) for v in obj]
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, (np.int32, np.int64)):
        return int(obj)
    return obj


def draw_forecast_trajectory(frame, forecast_path, forecast_center, track_id):
    """Draw a dotted trajectory arc and destination marker for a person heading toward a zone."""
    FORECAST_COLOR = (200, 100, 255)  # Purple-pink (fuchsia)
    DOT_RADIUS = 4
    DASH_LEN = 8
    GAP_LEN = 8

    for i in range(len(forecast_path) - 1):
        p1 = tuple(forecast_path[i])
        p2 = tuple(forecast_path[i + 1])

        # Draw dashed segment between successive points
        x1, y1 = p1
        x2, y2 = p2
        dist = max(1, int(np.hypot(x2 - x1, y2 - y1)))
        for d in range(0, dist, DASH_LEN + GAP_LEN):
            t0 = d / dist
            t1 = min(1.0, (d + DASH_LEN) / dist)
            sx = int(x1 + t0 * (x2 - x1))
            sy = int(y1 + t0 * (y2 - y1))
            ex = int(x1 + t1 * (x2 - x1))
            ey = int(y1 + t1 * (y2 - y1))
            cv2.line(frame, (sx, sy), (ex, ey), FORECAST_COLOR, 1)

    # Draw destination circle with pulsing visual (concentric rings)
    if forecast_center:
        fp = tuple(forecast_center)
        cv2.circle(frame, fp, DOT_RADIUS + 8, FORECAST_COLOR, 1)  # outer ring
        cv2.circle(frame, fp, DOT_RADIUS + 3, FORECAST_COLOR, 1)  # middle ring
        cv2.circle(frame, fp, DOT_RADIUS, FORECAST_COLOR, -1)     # filled center
        cv2.putText(frame, f"FORECAST #{track_id}", (fp[0] + 10, fp[1] - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, FORECAST_COLOR, 1)


load_dotenv()

from contextlib import asynccontextmanager

DEFAULT_ZONES = [
    {"id": "A1", "name": "Zone A1", "polygon": [[100,100], [400,100], [400,400], [100,400]], "active": True, "camera_source": "0"},
    {"id": "A2", "name": "Zone A2", "polygon": [[600,100], [900,100], [900,400], [600,400]], "active": True, "camera_source": "0"}
]

# State management - machine active per zone
machine_states = {zone["id"]: False for zone in DEFAULT_ZONES}
last_movement_time = {zone["id"]: 0 for zone in DEFAULT_ZONES}
manual_machine_control = {zone["id"]: False for zone in DEFAULT_ZONES}
danger_latch_time = {zone["id"]: 0 for zone in DEFAULT_ZONES}

# Real-time analytics cache
cached_stats = {"today_violations": 0, "safety_score": 100, "avg_reaction_time": 0, "distribution": {}, "trend": []}
cached_incidents = []


def sync_zone_runtime_state(zones):
    """Keep machine state dictionaries aligned with current zone IDs."""
    global machine_states, last_movement_time, manual_machine_control, danger_latch_time
    zone_ids = {z.get("id") for z in zones if isinstance(z, dict) and z.get("id")}
    machine_states = {zid: machine_states.get(zid, False) for zid in zone_ids}
    last_movement_time = {zid: last_movement_time.get(zid, 0) for zid in zone_ids}
    manual_machine_control = {zid: manual_machine_control.get(zid, False) for zid in zone_ids}
    danger_latch_time = {zid: danger_latch_time.get(zid, 0) for zid in zone_ids}

async def update_analytics_loop():
    """Background loop to update analytics from DB every 5 seconds."""
    global cached_stats, cached_incidents
    while True:
        try:
            # Update incidents and stats in parallel
            inc_task = alert_engine.get_incidents()
            stat_task = alert_engine.get_stats()
            
            inc_res, stat_res = await asyncio.gather(inc_task, stat_task)
            
            cached_incidents = inc_res
            cached_stats = stat_res
        except Exception as e:
            print(f"Analytics background loop error: {e}")
        
        await asyncio.sleep(5)

async def refresh_analytics_now():
    """Immediately refresh cached stats and incidents."""
    global cached_stats, cached_incidents
    try:
        inc_res, stat_res = await asyncio.gather(
            alert_engine.get_incidents(),
            alert_engine.get_stats()
        )
        cached_incidents = inc_res
        cached_stats = stat_res
    except Exception as e:
        print(f"Immediate refresh error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start analytics loop and acoustic detector
    asyncio.create_task(update_analytics_loop())
    acoustic_detector.start()

    # Attempt to load zones from Supabase
    try:
        zones = await alert_engine.get_zones()
        if zones is not None:
            global current_zones
            current_zones = zones
            sync_zone_runtime_state(current_zones)
    except Exception as e:
        print(f"Error loading zones: {e}")
    yield

    # Cleanup on shutdown
    acoustic_detector.stop()

app = FastAPI(title="CraneAI Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

detector     = CraneDetector(
    os.getenv("DETECTOR_MODEL", "yolov8n.pt"),
    use_pose=os.getenv("USE_POSE", "true").lower() == "true",
    use_sahi=os.getenv("USE_SAHI", "false").lower() == "true",
)
alert_engine    = AlertEngine()
fatigue_detector = FatigueDetector()
acoustic_detector = AcousticDetector()
camera_source   = os.getenv("CAMERA_SOURCE", 0)
camera_stream_lock = asyncio.Lock()
DETECTION_STRIDE    = max(1, int(os.getenv("DETECTION_STRIDE", "2")))
STREAM_TARGET_FPS   = max(8, int(os.getenv("STREAM_TARGET_FPS", "16")))
STREAM_JPEG_QUALITY = max(45, min(90, int(os.getenv("STREAM_JPEG_QUALITY", "68"))))

# Operator cabin camera (secondary) — index or RTSP URL
CABIN_CAMERA_SOURCE = os.getenv("CABIN_CAMERA_SOURCE", "")

# In-memory store for the latest annotated AR frame (bytes)
_ar_frame_bytes: bytes = b""

# Zones config (fallback to defaults if no DB/local data)
current_zones = list(DEFAULT_ZONES)

@app.get("/")
async def health_check():
    return {"status": "ok", "service": "CraneAI Backend", "websocket": "/ws/feed"}

@app.get("/incidents")
async def get_incidents():
    return await alert_engine.get_incidents()

@app.get("/stats")
async def get_stats():
    return await alert_engine.get_stats()

@app.post("/machine/state")
async def set_machine_state(data: dict):
    global machine_states, last_movement_time, manual_machine_control
    zone_id = data.get("zone_id")
    active = data.get("active", False)
    
    if zone_id:
        machine_states[zone_id] = active
        manual_machine_control[zone_id] = active
        if active:
            last_movement_time[zone_id] = time.time()
    else:
        # Toggle all if no zone id
        for zid in machine_states:
            machine_states[zid] = active
            manual_machine_control[zid] = active
            if active:
                last_movement_time[zid] = time.time()
            
    return {"status": "success", "machine_states": machine_states}

@app.get("/machine/state")
async def get_machine_state(zone_id: str = None):
    if zone_id:
        return {"active": machine_states.get(zone_id, False)}
    return machine_states

@app.post("/zones")
async def update_zones(payload: Any = Body(...)):
    global current_zones, machine_states
    if isinstance(payload, dict) and isinstance(payload.get("zones"), list):
        zones = payload.get("zones")
    elif isinstance(payload, list):
        zones = payload
    else:
        return {"status": "error", "message": "Invalid zones payload"}

    current_zones = zones
    sync_zone_runtime_state(current_zones)
    await alert_engine.replace_zones(zones)
    return {"status": "Zones updated"}

@app.post("/zones/toggle")
async def toggle_zone(data: dict):
    global current_zones
    zone_id = data.get("zone_id")
    enabled = data.get("enabled", True)
    
    for zone in current_zones:
        if zone['id'] == zone_id:
            zone['active'] = enabled
            await alert_engine.save_zone(zone)
            break
            
    return {"status": "success", "zones": current_zones}

@app.get("/zones")
async def get_zones():
    return current_zones

@app.get("/cameras")
async def get_cameras():
    return await alert_engine.get_cameras()

@app.post("/cameras")
async def save_cameras(cameras: list):
    await alert_engine.save_cameras(cameras)
    return {"status": "success"}


# ── Module 3B: Operator Fatigue ──────────────────────────────────────────────

def _build_sim_fatigue_frame(ear: float, perclos: float, alert: bool, closed_dur: float, t: float) -> str:
    """Generate a synthetic operator cabin frame for simulation mode."""
    frame = np.zeros((360, 480, 3), dtype=np.uint8)
    # Dark cabin background gradient
    for y in range(360):
        v = int(18 + (y / 360) * 10)
        frame[y, :] = [v, v + 2, v + 4]

    # ── Simulate head/face oval ──────────────────────────────────────────────
    cx, cy = 240, 165
    # Head
    cv2.ellipse(frame, (cx, cy), (70, 90), 0, 0, 360, (90, 75, 60), -1)
    # Hair
    cv2.ellipse(frame, (cx, cy - 20), (72, 55), 0, 180, 360, (40, 30, 22), -1)

    # ── Eyes: open/closed based on EAR ──────────────────────────────────────
    eye_open_h = max(2, int(18 * (ear / 0.35)))  # eye height scales with EAR
    eye_color  = (200, 210, 220)
    pupil_color = (30, 30, 40)
    iris_color  = (60, 100, 160)
    for ex in [cx - 28, cx + 28]:
        # Eye white
        cv2.ellipse(frame, (ex, cy - 5), (18, eye_open_h), 0, 0, 360, eye_color, -1)
        if eye_open_h > 6:
            # Iris
            cv2.circle(frame, (ex, cy - 5), 8, iris_color, -1)
            # Pupil
            cv2.circle(frame, (ex, cy - 5), 4, pupil_color, -1)
        # Eyelid overlay (heavier when closing)
        lid_h = int((1.0 - min(1.0, ear / 0.35)) * 20)
        if lid_h > 0:
            cv2.ellipse(frame, (ex, cy - 5 - eye_open_h + lid_h // 2),
                        (18, max(1, lid_h)), 0, 0, 360, (50, 38, 28), -1)

    # Nose
    cv2.ellipse(frame, (cx, cy + 20), (8, 5), 0, 0, 360, (80, 65, 52), -1)
    # Mouth
    cv2.ellipse(frame, (cx, cy + 45), (22, 8), 0, 0, 180, (70, 50, 45), 2)

    # ── Safety vest / shoulders ──────────────────────────────────────────────
    vest_color = (20, 140, 60) if not alert else (20, 20, 140)
    cv2.ellipse(frame, (cx, cy + 135), (90, 60), 0, 0, 360, vest_color, -1)
    # Reflective strips
    for dy in [110, 125]:
        cv2.line(frame, (cx - 85, cy + dy), (cx + 85, cy + dy), (220, 200, 40), 3)

    # ── HUD overlay ─────────────────────────────────────────────────────────
    color = (0, 0, 255) if alert else (0, 200, 160)
    status = "ALERT" if alert else ("DROWSY" if ear < 0.28 else "ALERT")  # "ALERT" = status OK when !alert
    status = "FATIGUE ALERT" if alert else ("DROWSY" if ear < 0.28 else "NORMAL")
    cv2.putText(frame, f"EAR: {ear:.3f}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    cv2.putText(frame, f"PERCLOS: {perclos:.1%}", (10, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    if closed_dur > 0.5:
        cv2.putText(frame, f"CLOSED: {closed_dur:.1f}s", (10, 72),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 80, 255), 2)
    # Status badge
    badge_col = (0, 0, 200) if alert else (0, 160, 80)
    cv2.rectangle(frame, (10, 310), (160, 345), badge_col, -1)
    cv2.putText(frame, status, (16, 333), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # Scan line effect
    for sl in range(0, 360, 6):
        cv2.line(frame, (0, sl), (480, sl), (0, 0, 0), 1)
        frame[sl, :] = (frame[sl, :] * 0.88).astype(np.uint8)

    # Red border on alert
    if alert:
        thickness = 8
        cv2.rectangle(frame, (0, 0), (479, 359), (0, 0, 255), thickness)

    # "SIMULATION" watermark
    cv2.putText(frame, "SIM MODE", (340, 350), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 80, 80), 1)

    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return base64.b64encode(buf).decode('utf-8')


_SIM_PERCLOS_WINDOW: list = []   # (timestamp, is_closed) for rolling window
_SIM_EYE_CLOSED_SINCE = None
_SIM_LAST_ALERT_TIME  = 0


def _simulate_fatigue(t: float) -> dict:
    """
    Generate realistic synthetic fatigue metrics.
    The operator goes through a 90-second cycle:
      0-30s  : alert (EAR ~0.35)
      30-50s : gradually drowsy (EAR falls to ~0.18)
      50-55s : micro-sleep (EAR < 0.12) → triggers EAR_CLOSURE alert
      55-70s : recovery
      70-90s : building PERCLOS from many blinks → PERCLOS alert
    """
    global _SIM_PERCLOS_WINDOW, _SIM_EYE_CLOSED_SINCE, _SIM_LAST_ALERT_TIME

    phase = t % 90.0

    if phase < 30:          # Alert phase
        base_ear = 0.35
        blink_rate = 0.06
    elif phase < 50:        # Drowsy onset
        prog = (phase - 30) / 20.0
        base_ear = 0.35 - prog * 0.18   # 0.35 → 0.17
        blink_rate = 0.15
    elif phase < 56:        # Micro-sleep / eyes closed
        base_ear = 0.10 + (phase - 50) * 0.01
        blink_rate = 0.0
    elif phase < 72:        # Recovery
        prog = (phase - 56) / 16.0
        base_ear = 0.10 + prog * 0.22
        blink_rate = 0.08
    else:                   # High blink / PERCLOS build
        base_ear = 0.28 + 0.06 * abs(np.sin(t * 0.8))
        blink_rate = 0.30

    # Add small natural noise + blink impulse
    noise = np.random.normal(0, 0.008)
    blink_impulse = -0.20 if np.random.random() < blink_rate * 0.1 else 0.0
    ear = float(np.clip(base_ear + noise + blink_impulse, 0.06, 0.45))

    now = time.time()
    is_closed = ear < 0.22

    # Continuous closure
    global _SIM_EYE_CLOSED_SINCE
    if is_closed:
        if _SIM_EYE_CLOSED_SINCE is None:
            _SIM_EYE_CLOSED_SINCE = now
        closed_dur = now - _SIM_EYE_CLOSED_SINCE
    else:
        _SIM_EYE_CLOSED_SINCE = None
        closed_dur = 0.0

    # PERCLOS rolling 60s
    _SIM_PERCLOS_WINDOW.append((now, is_closed))
    cutoff = now - 60.0
    _SIM_PERCLOS_WINDOW = [(ts, c) for ts, c in _SIM_PERCLOS_WINDOW if ts >= cutoff]
    perclos = (sum(1 for _, c in _SIM_PERCLOS_WINDOW if c) / max(1, len(_SIM_PERCLOS_WINDOW)))

    # Alert logic (30s cooldown)
    in_cooldown = (now - _SIM_LAST_ALERT_TIME) < 30.0
    alert = False
    alert_type = None
    if not in_cooldown:
        if closed_dur >= 2.0:
            alert = True
            alert_type = "EAR_CLOSURE"
            _SIM_LAST_ALERT_TIME = now
        elif perclos >= 0.35:
            alert = True
            alert_type = "PERCLOS"
            _SIM_LAST_ALERT_TIME = now

    return {
        "ear": round(ear, 3),
        "eyes_closed": is_closed,
        "closed_duration": round(closed_dur, 2),
        "perclos": round(perclos, 3),
        "alert": alert,
        "alert_type": alert_type,
        "simulation": True,
    }


@app.websocket("/ws/fatigue")
async def fatigue_ws(websocket: WebSocket):
    """
    Secondary WebSocket for cabin-camera fatigue monitoring.
    Tries the real cabin camera + MediaPipe first; falls back to full
    simulation mode so the frontend always has live data to display.
    """
    await websocket.accept()
    cap = None
    simulation_mode = False

    # ── Determine whether we can use a real camera + mediapipe ──────────────
    src = CABIN_CAMERA_SOURCE
    if src and fatigue_detector._ready:
        try:
            src_val = int(src) if str(src).isdigit() else src
            cap = cv2.VideoCapture(src_val)
            if not cap.isOpened():
                cap.release()
                cap = None
        except Exception:
            cap = None

    if cap is None or not fatigue_detector._ready:
        simulation_mode = True
        if cap:
            cap.release()
            cap = None
        print("[FatigueWS] Entering SIMULATION mode (no camera or mediapipe unavailable).")

    try:
        executor = __import__('concurrent.futures', fromlist=['ThreadPoolExecutor']).ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_event_loop()
        sim_start = time.time()

        while True:
            if simulation_mode:
                t = time.time() - sim_start
                fatigue_data = _simulate_fatigue(t)
                frame_b64 = _build_sim_fatigue_frame(
                    fatigue_data["ear"],
                    fatigue_data["perclos"],
                    fatigue_data["alert"],
                    fatigue_data["closed_duration"],
                    t,
                )
            else:
                ok, frame = cap.read()
                if not ok:
                    await asyncio.sleep(0.1)
                    continue
                fatigue_data = await loop.run_in_executor(
                    executor, fatigue_detector.analyze_frame, frame.copy()
                )
                fatigue_data["simulation"] = False
                annotated = fatigue_detector.draw_overlay(frame, fatigue_data)
                _, buf = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
                frame_b64 = base64.b64encode(buf).decode('utf-8')

            payload = {
                **fatigue_data,
                "frame": frame_b64,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if fatigue_data.get("alert"):
                asyncio.create_task(alert_engine.save_incident(
                    "CABIN", "Operator Cabin",
                    type=f"FATIGUE_{fatigue_data.get('alert_type', 'UNKNOWN')}",
                    severity="CRITICAL"
                ))

            await websocket.send_json(make_serializable(payload))
            await asyncio.sleep(0.1)   # 10 FPS

    except WebSocketDisconnect:
        print("Fatigue WS disconnected")
    except Exception as e:
        print(f"Fatigue WS error: {e}")
    finally:
        if cap:
            cap.release()


# ── Module 3C: Acoustic Events ────────────────────────────────────────────
@app.get("/acoustic/events")
async def get_acoustic_events():
    """Return latest detected acoustic events (bang, crash, scream, alarm, etc.)."""
    return acoustic_detector.get_latest_events()


# ── Module 3D: AR Safety Overlay ────────────────────────────────────────
@app.get("/ar/stream", response_class=StreamingResponse)
async def ar_mjpeg_stream():
    """
    Module 3D: MJPEG stream of the annotated safety overlay.
    Compatible with smart glasses (Vuzix, RealWear) and any MJPEG viewer.
    Serves the latest annotated frame captured by the /ws/feed pipeline.
    """
    async def frame_generator():
        global _ar_frame_bytes
        while True:
            if _ar_frame_bytes:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" +
                       _ar_frame_bytes + b"\r\n")
            await asyncio.sleep(1.0 / STREAM_TARGET_FPS)

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# ── Module 3E: Digital Twin Data ─────────────────────────────────────────
_latest_twin_data: dict = {}

@app.get("/twin/state")
async def get_twin_state():
    """
    Module 3E: Returns the latest normalised site state for the Digital Twin.
    Includes crane positions, tracked workers, and zone polygons — all in
    a [0.0, 1.0] normalised coordinate space relative to frame dimensions.
    """
    return _latest_twin_data


# ── Module 3F: AI Shift Report ───────────────────────────────────────────
@app.post("/report/generate")
async def generate_report(payload: dict = Body(...)):
    """
    Module 3F: Trigger AI shift report generation.
    Body: { "shift_start": "ISO8601", "shift_end": "ISO8601" }
    Response: { "status", "report_text", "pdf_url", "telegram_sent" }
    """
    shift_start = payload.get("shift_start", "")
    shift_end   = payload.get("shift_end", datetime.now(timezone.utc).isoformat())
    incidents   = await alert_engine.get_incidents(limit=500)
    result      = await generate_shift_report(incidents, shift_start, shift_end)
    return result


# ── Module 2e: Calibration Log Flush ────────────────────────────────────
@app.get("/calibration/log")
async def get_calibration_log():
    """Returns and flushes the pending calibration event log."""
    events = detector.flush_calibration_log()
    return {"events": events, "count": len(events)}

@app.post("/calibration/flag")
async def flag_calibration_event(data: dict = Body(...)):
    """Allow frontend to flag a specific track as a false positive or negative."""
    detector.log_calibration_event(
        event_type=data.get("event_type", "FALSE_POSITIVE"),
        track_id=data.get("track_id", -1),
        confidence=data.get("confidence", 0.0)
    )
    return {"status": "logged"}


@app.websocket("/ws/feed")
async def websocket_endpoint(websocket: WebSocket, camera_id: str = "0"):
    await websocket.accept()
    
    cap = None
    try:
        # Find camera source from ID
        cameras = await alert_engine.get_cameras()
        cam = next((c for c in cameras if str(c.get("id")) == str(camera_id)), None)
        src = cam.get("source", camera_source) if cam else camera_source
        
        if str(src).isdigit():
            src = int(src)
            
        print(f"Attempting to open camera source: {src}")
        
        # Prefer DSHOW first on Windows because MSMF can fail under reconnect churn.
        is_windows = platform.system().lower().startswith("win")
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if is_windows else [cv2.CAP_ANY]
        
        def try_open(source, backend_list):
            for b in backend_list:
                c = cv2.VideoCapture(source, b) if isinstance(source, int) else cv2.VideoCapture(source)
                if c.isOpened():
                    return c
                c.release()
            return None

        cap = try_open(src, backends)

        if not cap or not cap.isOpened():
            print(f"WARNING: No camera found. Running in SIMULATION MODE with synthetic frames.")
            is_simulation = True
        else:
            is_simulation = False
            # Better input detail improves person detection quality in wider shots.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_ZOOM, 0)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Minimize latency by keeping only the latest frame

        read_failures = 0
        frame_index = 0
        cached_detections = []

        while True:
            loop_start = time.perf_counter()
            if is_simulation:
                # Generate a rich synthetic frame simulating an industrial site
                frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                t = time.time()
                # Dark concrete background
                cv2.rectangle(frame, (0, 0), (1280, 720), (25, 28, 32), -1)
                # Ground line
                cv2.line(frame, (0, 580), (1280, 580), (60, 65, 70), 2)
                # Crane structure (static)
                cv2.rectangle(frame, (900, 100), (960, 580), (80, 90, 100), -1)  # Crane tower
                cv2.rectangle(frame, (900, 100), (1150, 130), (80, 90, 100), -1) # Crane arm
                cv2.line(frame, (1150, 130), (1150, 300), (100, 110, 120), 2)    # Crane cable
                cv2.rectangle(frame, (1120, 300), (1180, 340), (100, 120, 140), -1) # Hook
                # Safety zone polygon
                zone_pts = np.array([[800,580],[1200,580],[1200,400],[800,400]], np.int32)
                cv2.polylines(frame, [zone_pts], True, (0, 200, 100), 2)
                cv2.putText(frame, "ZONE A1", (805, 395), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 100), 2)
                # Moving worker
                px = int(640 + 350 * np.cos(t * 0.4))
                py = int(500 + 50 * np.sin(t * 0.6))
                # Worker body
                cv2.rectangle(frame, (px-15, py-60), (px+15, py+20), (0, 180, 255), -1)
                cv2.circle(frame, (px, py-75), 20, (0, 200, 255), -1)  # Head
                # Second worker
                px2 = int(300 + 100 * np.sin(t * 0.3))
                cv2.rectangle(frame, (px2-15, py-60), (px2+15, py+20), (0, 180, 255), -1)
                cv2.circle(frame, (px2, py-75), 20, (0, 200, 255), -1)
                # Overlay text
                cv2.rectangle(frame, (0, 0), (1280, 60), (15, 18, 22), -1)
                cv2.putText(frame, "CRANEGUARD AI  |  DEMO SIMULATION MODE", (30, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 220, 180), 2)
                # Timestamp
                ts = time.strftime("%Y-%m-%d  %H:%M:%S  UTC")
                cv2.putText(frame, ts, (900, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 130, 140), 1)
                success = True
            else:
                success, frame = cap.read()
                if not success:
                    read_failures += 1
                    if read_failures > 20:
                        print("Camera read failed repeatedly; closing stream")
                        break
                    await asyncio.sleep(0.05)
                    continue

            read_failures = 0
            frame_index += 1
                
            # Run Inference
            if frame_index % DETECTION_STRIDE == 0 or not cached_detections:
                cached_detections = detector.detect_and_track(frame)
            detections = cached_detections
            
            # Check Zones (Filtered by active camera)
            active_zones = [z for z in current_zones if str(z.get('camera_source', '0')) == str(camera_id)]
            zone_results = check_zones(detections, active_zones)
            
            # Process Alerts
            alerts = []
            curr_time = time.time()
            for zone_id, status in zone_results.items():
                has_worker = status['worker_count'] > 0
                is_manual_mode = manual_machine_control.get(zone_id, False)
                
                # AUTOMATIC ACTIVATION: If crane detected or movement detected, start machine for this zone
                # AUTO-FIX: Do not allow AI to start machine if a worker is in the zone!
                if status['movement'] and not has_worker and not is_manual_mode:
                    machine_states[zone_id] = True
                    last_movement_time[zone_id] = curr_time
                elif (not is_manual_mode) and curr_time - last_movement_time.get(zone_id, 0) > 0.8:
                    # After 0.8 seconds of stillness, revert to IDLE for snappy response
                    machine_states[zone_id] = False
                
                zone_machine_active = machine_states.get(zone_id, False)
                # DANGER condition: Person in zone AND (Machine active via AI or manual)
                is_danger = has_worker and zone_machine_active
                collision_risk = status.get('collision_risk')
                collision_with_running_machine = collision_risk and zone_machine_active
                
                is_new_danger = False
                latch_active = (curr_time - danger_latch_time.get(zone_id, 0)) < 3.0
                
                if collision_with_running_machine:
                    is_danger = True

                if is_danger and not latch_active:
                    is_new_danger = True
                    danger_latch_time[zone_id] = curr_time
                elif is_danger:
                    danger_latch_time[zone_id] = curr_time
                    
                latched_danger = is_danger or latch_active

                # AUTOMATIC FIX: If danger is detected, instantly cut off the machine
                if is_danger:
                    machine_states[zone_id] = False
                    manual_machine_control[zone_id] = False
                    zone_machine_active = False
                    last_movement_time[zone_id] = 0

                status['danger'] = latched_danger 
                status['machine_active'] = zone_machine_active
                status['warning'] = status['worker_count'] > 0 # Warning if person is in zone at all
                
                if latched_danger:
                    if collision_risk:
                        alerts.append(f"CRITICAL: {status['name']} - Person TOUCHING Machine!")
                        if is_new_danger:
                            asyncio.create_task(alert_engine.save_incident(zone_id, status['name'], type="PROXIMITY_VIOLATION", frame=frame))
                            asyncio.create_task(refresh_analytics_now())
                    else:
                        alerts.append(f"CRITICAL: {status['name']} - Worker in Danger Zone!")
                        if is_new_danger:
                            asyncio.create_task(alert_engine.save_incident(zone_id, status['name'], frame=frame))
                            asyncio.create_task(refresh_analytics_now())
            # ── FORECAST / PRE-COLLISION ALERT GENERATION ───────────────────────────
            # Only issue a PRE-COLLISION if the machine is ACTIVE — no point
            # warning about a stationary crane. We also skip if the zone is
            # already in a full DANGER state (the higher-priority alert covers it).
            forecast_ids = status.get('forecast_violation_ids', [])
            already_danger = latched_danger
            if forecast_ids and zone_machine_active and not already_danger:
                worker_list = ', '.join(f'#{fid}' for fid in forecast_ids)
                pre_col_msg = f"PRE-COLLISION: {status['name']} - Worker {worker_list} approaching at speed!"
                if pre_col_msg not in alerts:
                    alerts.append(pre_col_msg)
                    asyncio.create_task(alert_engine.save_incident(
                        zone_id, status['name'],
                        type="PRE_COLLISION_WARNING",
                        severity="WARNING",
                        frame=frame
                    ))
            elif not latched_danger and status.get('warning'):
                alerts.append(f"WARNING: {status['name']} - Dont start the machine")


            # Encode and Send
            for det in detections:
                # SKIP detection if it is inside a DISABLED active zone
                skip_det = False
                for zone in active_zones:
                    if not zone.get('active', True):
                        if is_box_in_zone(det['bbox'], zone['polygon']):
                            skip_det = True
                            break
                if skip_det: continue

                bbox = det['bbox']
                color = (0, 255, 0)
                if det['class'] == 'person': color = (0, 255, 255) # Yellow for persons
                cv2.rectangle(frame, (bbox[0], bbox[1]), (bbox[2], bbox[3]), color, 2)
                cv2.putText(frame, f"{det['class']} {det['id']}", (bbox[0], bbox[1]-10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                
                # ── DRAW FORECAST TRAJECTORY ARC ─────────────────────────────
                # Always draw the trajectory for persons so operators can
                # see where each worker is heading. This fires regardless
                # of whether a zone violation is forecasted.
                if det['class'] == 'person' and det.get('forecast_path'):
                    draw_forecast_trajectory(
                        frame,
                        det['forecast_path'],
                        det.get('forecast_center'),
                        det['id']
                    )

            for zone in active_zones:
                is_active = zone.get('active', True)
                points = [tuple(p) for p in zone['polygon']]
                
                # Determine Color
                if not is_active:
                    color = (128, 128, 128) # GRAY for OFF zones
                else:
                    result = zone_results.get(zone['id'], {})
                    is_danger = result.get('danger', False)
                    worker_count = result.get('worker_count', 0)
                    
                    if is_danger: color = (0, 0, 255) # RED
                    elif worker_count > 0: color = (0, 255, 255) # YELLOW
                    else: color = (0, 255, 0) # GREEN
                
                for i in range(len(points)):
                    cv2.line(frame, points[i], points[(i+1)%len(points)], color, 2)
                    label = f"{zone['name']} [OFF]" if not is_active else zone['name']
                    cv2.putText(frame, label, (points[0][0], points[0][1] - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            # Encode and Send
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])

            # ── Module 3D: Store latest annotated frame for AR MJPEG stream ──
            global _ar_frame_bytes
            _ar_frame_bytes = buffer.tobytes()

            frame_base64 = base64.b64encode(buffer).decode('utf-8')

            # ── Module 3C: Get acoustic events + correlate with danger zones ──
            danger_zone_ids = [zid for zid, st in zone_results.items() if st.get("danger")]
            acoustic_detector.set_active_zones(danger_zone_ids)
            acoustic_events = acoustic_detector.get_latest_events()

            # ── Module 3C: Escalate alerts for correlated acoustic events ─────
            for ev in acoustic_events[:3]:   # Only top 3 most recent
                if ev.get("escalated"):
                    alerts.append(f"ACOUSTIC ALERT: {ev['label']} detected near active danger zone!")

            # ── Module 3E: Build Digital Twin normalised state ─────────────────
            frame_h, frame_w = frame.shape[:2]
            twin_workers = [
                {
                    "id": d["id"],
                    "x": d["center"][0] / frame_w,
                    "y": d["center"][1] / frame_h,
                    "risk_score": d.get("risk_score", 0.0),
                    "forecast_x": d["forecast_center"][0] / frame_w if d.get("forecast_center") else None,
                    "forecast_y": d["forecast_center"][1] / frame_h if d.get("forecast_center") else None,
                }
                for d in detections if d.get("class") == "person"
            ]
            twin_cranes = [
                {
                    "id": d["id"],
                    "x": d["center"][0] / frame_w,
                    "y": d["center"][1] / frame_h,
                    "class": d["class"],
                }
                for d in detections if d.get("class") in ("truck", "bus", "forklift", "train")
            ]
            global _latest_twin_data
            _latest_twin_data = {
                "workers": twin_workers,
                "cranes":  twin_cranes,
                "zones":   [
                    {
                        "id": z["id"], "name": z["name"],
                        "polygon": [
                            [pt[0] / frame_w, pt[1] / frame_h]
                            for pt in z["polygon"]
                        ],
                        "danger": zone_results.get(z["id"], {}).get("danger", False),
                        "active": z.get("active", True),
                    }
                    for z in active_zones
                ],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            payload = {
                "frame": frame_base64,
                "alerts": alerts,
                "detections": detections,
                "zone_status": zone_results,
                "machine_states": machine_states,
                "stats": cached_stats,
                "incidents": cached_incidents,
                "zones": current_zones,
                "acoustic_events": acoustic_events[:5],   # Latest 5 acoustic events
                "twin_state": _latest_twin_data,           # Digital twin payload
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            await websocket.send_json(make_serializable(payload))
            elapsed = time.perf_counter() - loop_start
            target_frame_time = 1.0 / STREAM_TARGET_FPS
            await asyncio.sleep(max(0, target_frame_time - elapsed))

    except WebSocketDisconnect:
        print("Client disconnected ")
    except Exception as e:
        print(f"WS error: {e}")
    finally:
        if cap:
            cap.release()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8200)
