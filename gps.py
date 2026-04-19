"""
GPS position store.

In production, wire this to gpsd or a serial NMEA receiver.
The frontend sends browser-GPS fixes via the WebSocket, which calls update_position().
"""
from typing import Optional, Tuple

_lat: Optional[float] = None
_lon: Optional[float] = None


def update_position(lat: float, lon: float) -> None:
    global _lat, _lon
    _lat = lat
    _lon = lon


def get_position() -> Tuple[Optional[float], Optional[float]]:
    return _lat, _lon
