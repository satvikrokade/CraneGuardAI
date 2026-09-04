---
title: CraneGuard Safety Monitoring
emoji: 🏗️
colorFrom: blue
colorTo: green
sdk: static
pinned: false
---

# CraneGuard: Safety Monitoring System

**CraneGuard** is a full-stack safety monitoring solution designed for industrial environments. It provides real-time protection for workers in high-risk zones near heavy machinery.

## Features

*   **Live Monitoring Feed**: Real-time person, crane, and forklift detection with low-latency streaming.
*   **Dynamic Safety Zones**: Draw and manage safety polygons directly in the web UI.
*   **Analytics Dashboard**: Comprehensive view of safety trends, violation logs, and performance metrics.
*   **Risk Heatmaps**: Spatial visualization of high-incident areas.
*   **Instant Alerts**: Multi-channel notifications via Telegram and local UI banners with captured snapshots.
*   **Cloud Integration**: Persistent storage of configurations and incident logs.

## Tech Stack

### Backend
- **Framework**: FastAPI
- **Computer Vision**: OpenCV
- **Database**: Supabase (Postgres)

### Frontend
- **Framework**: React + Vite
- **Styling**: TailwindCSS
- **Charts**: Recharts

## Documentation

For a deep dive into the architecture, API reference, and troubleshooting, check out our:
👉 **[Detailed Project Documentation](./documentation.md)**

## Quick Start

### 1. Backend Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Frontend Setup
```bash
cd frontend
npm install
npm run dev
```

### 3. Environment Configuration
Create a `.env` in the `backend/` folder:
```env
SUPABASE_URL=your_url
SUPABASE_KEY=your_key
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
# Use 0 for Webcam, or a URL (e.g. http://192.168.x.x:8080/video) for a mobile camera
CAMERA_SOURCE=0  
```

## Deployment on Render.com

To deploy CraneGuard on Render.com:
1. Connect this repository to your Render account.
2. In the Render Dashboard, create a **New Blueprint Instance**.
3. Select this repository. Render will automatically detect the `render.yaml` file and set up both the **craneguard-backend** (Docker) and **craneguard-frontend** (Node.js) services.
4. Set the necessary environment variables (`SUPABASE_URL`, `SUPABASE_KEY`, etc.) in the Render dashboard for the backend service.

Alternatively, you can manually create a Web Service for the backend using the provided `Dockerfile` in the `backend` folder, and a Static Site or Node Web Service for the `frontend` folder using `npm run build`.

## License
Distributed under the MIT License. See `LICENSE` for more information.
