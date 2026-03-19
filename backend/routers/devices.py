"""
Devices API endpoints - enumerate local video capture devices
"""
from fastapi import APIRouter
from typing import List

from ..models import DeviceInfo
from ..services.capture_backends import list_all_devices

router = APIRouter()


@router.get("/", response_model=List[DeviceInfo])
async def get_devices():
    """List available video capture devices across all backends"""
    return list_all_devices()
