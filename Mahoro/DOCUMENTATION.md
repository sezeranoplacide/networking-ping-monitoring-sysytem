# 📡 Network Monitor Pro - Professional Ping Monitoring System

## Overview

**Network Monitor Pro** is a comprehensive, enterprise-grade network device monitoring solution with real-time dashboard, advanced analytics, status tracking, and professional alert management. Designed for network administrators and IT professionals who need robust network health monitoring.

## Key Differentiators

- ✅ **Professional Dashboard** - Real-time network overview with key metrics
- ✅ **Real-time Monitoring** - Live device status with 5-second automatic refresh
- ✅ **Advanced Analytics** - Comprehensive performance metrics (uptime %, latency min/max/avg)
- ✅ **Status Timeline** - Visual device status transitions (UP 📈 / DOWN 📉)
- ✅ **Device Grouping** - Organize devices by functionality/category
- ✅ **Alert System** - Intelligent alerts with acknowledgment tracking
- ✅ **Historical Recording** - Complete ping history with latency trends
- ✅ **Network Health** - Overall health percentage and uptime calculations
- ✅ **Performance Charts** - Interactive charts for status distribution and latency
- ✅ **Professional UI** - Modern responsive interface with smooth animations

## Features in Detail

### Dashboard Tab
Central hub for network overview:
- **6 Summary Cards**: Total Devices, Online Count, Offline Count, Network Health %, Average Latency, Active Alerts
- **Status Distribution Chart**: Doughnut chart showing online/offline/unknown breakdown
- **Latency Comparison Chart**: Bar chart comparing device latencies
- **Grouped Devices**: Devices organized by category with group health metrics

### Devices Tab
Complete device management:
- **Add Device Form**: Device name, IP address, ping interval, timeout, group assignment
- **Device Grid**: All monitored devices with status badges and latency info
- **Device Actions**: Edit, Details modal, Delete
- **Device Details Modal**: Complete statistics including uptime %, latency metrics, total pings

### Analytics Tab
Detailed performance analysis:
- **Statistics Table**: Device performance data (uptime, avg/min/max latency, status changes)
- **Timeline Viewer**: Status change history with up/down markers and durations
- **Device Selector**: Filter timeline by specific device

### Alerts Tab
Professional alert management:
- **Alert Filtering**: View all alerts or only unacknowledged
- **Alert List**: Color-coded by severity (Critical/Warning/Info)
- **Acknowledgment**: Mark alerts as reviewed
- **Timestamp**: Complete audit trail

## Technical Architecture

### Backend (Flask + Python)
```
app.py - REST API with 20+ endpoints
├── Device Management (CRUD)
├── Statistics & Analytics
├── Timeline & History
├── Alerts & Acknowledgment
└── Device Groups
```

### Frontend (HTML/CSS/JavaScript)
```
index.html - Multi-tab professional interface
static/style.css - 1000+ lines of professional styling
static/app.js - Interactive state management & API integration
```

### Database (SQLite)
```
ping_monitor.sqlite3 - 6 tables with performance indices
├── devices
├── ping_results
├── status_changes
├── alerts
├── device_groups
└── Indices for performance optimization
```

## Database Schema

### Table: devices
- id, name, ip_address, status, interval, timeout, group_name
- last_latency_ms, min_latency_ms, max_latency_ms, avg_latency_ms
- uptime_percentage, last_seen_at, created_at, updated_at

### Table: ping_results
- id, device_id, status, latency_ms, jitter_ms, packet_loss, timestamp

### Table: status_changes
- id, device_id, from_status, to_status, duration_seconds, recorded_at

### Table: alerts
- id, device_id, alert_type, severity, message, is_acknowledged, created_at

### Table: device_groups
- id, name, description, color

## API Endpoints (20+)

### Device Management
- `GET /api/devices` - List all devices
- `POST /api/devices` - Create device
- `GET /api/devices/<id>` - Get device details
- `PUT /api/devices/<id>` - Update device
- `DELETE /api/devices/<id>` - Delete device
- `POST /api/devices/<id>/assign-group` - Assign to group

### Network Summary
- `GET /api/network/summary` - Overall network health

### Device Analytics
- `GET /api/devices/<id>/statistics` - Performance metrics (24h)
- `GET /api/devices/<id>/history?limit=100` - Ping history
- `GET /api/devices/<id>/timeline?limit=50` - Status transitions

### Alerts
- `GET /api/alerts` - List alerts
- `POST /api/alerts/<id>/acknowledge` - Mark as reviewed
- `POST /api/devices/<id>/alert` - Create alert

### Groups
- `GET /api/groups` - List groups
- `POST /api/groups` - Create group

## Installation & Setup

### Prerequisites
- Python 3.9+
- Modern web browser
- ~100MB disk space for database

### Install
```bash
cd Mahoro
pip install -r requirements.txt
```

### Run
```bash
python app.py
# or
run.bat
```

Access at: **http://localhost:5000**

## Configuration

### Device Settings
- **Name**: Display name
- **IP Address**: IPv4 to monitor
- **Interval**: Ping frequency (seconds)
- **Timeout**: Response wait time (seconds)
- **Group**: Optional categorization

### Application
```python
app.run(debug=True, host='0.0.0.0', port=5000)
# Modify in app.py to change port/host
```

## User Interface

### Color Coding
- **Green** - Online/Success
- **Red** - Offline/Danger
- **Gray** - Unknown
- **Blue-Purple** - Primary/Accent
- **Orange** - Warnings/Alerts

### Navigation
- **Dashboard** - Real-time overview
- **Devices** - Management & configuration
- **Analytics** - Performance analysis
- **Alerts** - System notifications

## Performance

### Automatic Updates
- Dashboard refreshes every 5 seconds
- Charts update on data change
- Database indices optimize queries

### Scalability
- SQLite suitable for up to 1000+ devices
- Consider archiving old ping history for large deployments
- Can be migrated to PostgreSQL for enterprise scale

## Security

### Built-in Protections
- XSS Protection (HTML escaping)
- Input validation
- CORS enabled for cross-origin requests
- Error handling without sensitive data

### Production Recommendations
- Use HTTPS/SSL
- Add authentication layer
- Enable database encryption
- Implement rate limiting
- Add CSRF token validation

## Troubleshooting

### Port 5000 in Use
```python
# Edit app.py, line at end:
app.run(debug=True, host='0.0.0.0', port=8080)  # Change port
```

### Database Reset
```bash
# Delete database to start fresh (removes all data)
rm ping_monitor/data/ping_monitor.sqlite3
# Will recreate on next run
```

### Dashboard Not Loading
1. Check Flask is running
2. Verify http://localhost:5000 loads
3. Check browser console for errors (F12 > Console)
4. Ensure JavaScript is enabled
5. Clear browser cache

### Devices Not Updating
- Verify Flask is running
- Check network connectivity
- Confirm devices are reachable
- Check browser console for API errors

## Integration Guide

### Recording Ping Results
```python
from ping_monitor.device_manager import DeviceManager

dm = DeviceManager()
dm.record_ping_result(
    device_id=1,
    status='online',
    latency_ms=25.5
)
```

### Creating Alerts
```python
dm.create_alert(
    device_id=1,
    alert_type='status_change',
    severity='warning',
    message='Device went offline'
)
```

### Creating Groups
```python
dm.create_device_group(
    name='Critical Servers',
    description='Important servers',
    color='#e74c3c'
)
```

## Deployment Options

### Development
```bash
python app.py  # Debug mode enabled
```

### Production with Gunicorn
```bash
pip install gunicorn
gunicorn app:app -w 4 -b 0.0.0.0:5000
```

### Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "app.py"]
```

## File Structure
```
Mahoro/
├── app.py                      # Flask web server
├── requirements.txt            # Python dependencies
├── run.bat                     # Windows launcher
├── README.md                   # Quick start guide
├── DOCUMENTATION.md            # Full documentation
│
├── ping_monitor/
│   ├── __init__.py
│   ├── device_manager.py       # Database & business logic
│   └── data/
│       └── ping_monitor.sqlite3
│
├── templates/
│   └── index.html              # Web interface
│
└── static/
    ├── style.css               # Professional styling
    ├── app.js                  # Frontend logic
    └── (Chart.js external)
```

## Dependencies

### Python Packages
- `Flask==3.0.0` - Web framework
- `Flask-CORS==4.0.0` - Cross-origin requests

### Frontend Libraries
- `Chart.js 4.4.0` - Data visualization (loaded from CDN)

## Performance Tips

- Monitor database size (ping_monitor/data/ping_monitor.sqlite3)
- Archive ping history monthly for large deployments
- Adjust refresh intervals based on network size
- Use device groups for 100+ device deployments
- Consider PostgreSQL migration for 1000+ devices

## Support & Troubleshooting

**Issue: Port 5000 already in use**
- Change port in app.py or run on different port

**Issue: No data loading**
- Check browser network tab for failed requests
- Verify Flask server is running
- Check browser console for errors

**Issue: High CPU/Memory usage**
- Reduce refresh interval if set too low
- Archive old ping history
- Restart Flask server periodically

## Version Information

- **Version**: 1.0.0 Professional Edition
- **Python**: 3.9+
- **License**: MIT
- **Design**: Enterprise-grade network monitoring

## Contact & Support

For issues or questions:
1. Check Troubleshooting section
2. Verify dependencies installed
3. Check browser console (F12)
4. Review application logs

---

Last Updated: 2026-07-25  
Designed for enterprise network monitoring
