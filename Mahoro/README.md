# � Network Monitor Pro - Professional Ping Monitoring System

A comprehensive, enterprise-grade network device monitoring solution with real-time dashboard, advanced analytics, status tracking, and professional reporting capabilities.

## 🎯 Overview

**Network Monitor Pro** is a sophisticated ping monitoring tool designed for network administrators and IT professionals. It provides continuous network device monitoring with real-time status updates, detailed analytics, historical tracking, and professional alert management.

### Key Differentiators
- ✅ **Professional Dashboard** - Network overview with key metrics at a glance
- ✅ **Real-time Monitoring** - Live device status with automatic 5-second refresh
- ✅ **Advanced Analytics** - Comprehensive performance metrics and statistics (24-hour tracking)
- ✅ **Status Timeline** - Visual representation of device status transitions (UP/DOWN movements)
- ✅ **Device Grouping** - Organize devices by category for better management
- ✅ **Alert System** - Intelligent alert management with acknowledgment tracking
- ✅ **Historical Recording** - Complete ping history with latency trends
- ✅ **Network Health** - Overall network health percentage and uptime tracking
- ✅ **Performance Charts** - Interactive charts for status distribution and latency analysis
- ✅ **Professional UI** - Modern, responsive interface with smooth animations

## Features

✅ **Device Management** - Add, edit, and delete devices to monitor  
✅ **Real-time Updates** - Live status and latency monitoring (5-second refresh)  
✅ **Beautiful Dashboard** - Modern, responsive web interface  
✅ **Status Charts** - Visual distribution of online/offline/unknown devices  
✅ **Latency Tracking** - Monitor and chart ping latency  
✅ **Ping History** - View detailed ping history with charts  
✅ **Database** - SQLite persistence  

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Web Server
```bash
python app.py
```

The web interface will be available at: **http://localhost:5000**

### 3. Use the Dashboard

#### Adding a Device
1. Fill in the "Add Device" form with:
   - Device Name (e.g., "Router", "Gateway")
   - IP Address (e.g., 192.168.1.1)
   - Interval: How often to ping (seconds)
   - Timeout: Maximum wait time (seconds)
2. Click "Add Device"

#### Monitoring Devices
- View all devices in the grid
- **Status badges** show real-time status (Online/Offline/Unknown)
- **Latency display** shows the last ping time
- **Charts** display status distribution and latency comparison

#### Device Actions
- **Edit** - Modify interval/timeout settings
- **Details** - View full device info and ping history chart
- **Delete** - Remove device from monitoring

## 📋 File Structure
```
Mahoro/
├── app.py                           # Flask web server
├── requirements.txt                 # Python dependencies
├── run.bat                          # Windows launch script
├── README.md                        # Documentation
│
├── ping_monitor/
│   ├── __init__.py
│   ├── device_manager.py            # Database & business logic
│   └── data/
│       └── ping_monitor.sqlite3     # SQLite database
│
├── templates/
│   └── index.html                   # Main web interface
│
└── static/
    ├── style.css                    # Professional styling
    └── app.js                       # Interactive frontend
```

## 🔌 API Endpoints

### Device Management
```
GET    /api/devices                    # List all devices
POST   /api/devices                    # Create device
GET    /api/devices/<id>               # Get device details
PUT    /api/devices/<id>               # Update device
DELETE /api/devices/<id>               # Delete device
POST   /api/devices/<id>/assign-group  # Assign to group
```

### Network Information
```
GET    /api/network/summary            # Network overview statistics
```

### Device Analytics
```
GET    /api/devices/<id>/statistics    # Performance metrics (24h)
GET    /api/devices/<id>/history       # Ping history records
GET    /api/devices/<id>/timeline      # Status change timeline
```

### Alerts
```
GET    /api/alerts                     # List alerts
POST   /api/alerts/<id>/acknowledge    # Mark alert as reviewed
POST   /api/devices/<id>/alert         # Create alert
```

### Groups
```
GET    /api/groups                     # List device groups
POST   /api/groups                     # Create group
```

## 📊 Database Schema

### Tables
- **devices** - Device configurations and current status
- **ping_results** - Historical ping records with latency data
- **status_changes** - Timeline of UP/DOWN transitions
- **alerts** - Alert records with acknowledgment tracking
- **device_groups** - Custom device groupings
- **status_changes** - Device status transition history

### Data Storage
- Database: SQLite (`ping_monitor/data/ping_monitor.sqlite3`)
- Automatic backup-friendly structure
- Indexed for performance optimization

## Default Settings

- **Ping Interval**: 5 seconds (configurable per device)
- **Timeout**: 2 seconds (configurable per device)
- **Web Refresh**: 5 seconds
- **History Limit**: 100 records per device

## Troubleshooting

### Port Already in Use
If port 5000 is in use, modify `app.py`:
```python
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)  # Change port here
```

### Database Issues
To reset the database, delete:
```
ping_monitor/data/ping_monitor.sqlite3
```
It will be recreated on next run.

### Permission Issues (macOS/Linux)
```bash
chmod +x app.py
```

## Development

The frontend uses:
- **HTML5** for structure
- **CSS3** for styling with gradients and animations
- **Vanilla JavaScript** for interactivity (no frameworks)
- **Chart.js** for data visualization

The backend uses:
- **Flask** for HTTP server
- **Flask-CORS** for cross-origin requests
- **SQLite** for data persistence
- **Python 3.9+**

## Notes

- Devices are monitored in real-time but require a separate monitoring service to actually ping devices
- The frontend displays status as set by the monitoring service
- This is the web UI layer - integrate with your ping monitoring logic separately

## License

MIT
