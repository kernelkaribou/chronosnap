"""
Devices API endpoints - enumerate local video capture devices
"""
from fastapi import APIRouter
from typing import List

from ..models import DeviceInfo
from ..services.device_manager import list_video_devices

router = APIRouter()


@router.get("/", response_model=List[DeviceInfo])
async def get_devices():
    """List available V4L2 video capture devices"""
    return list_video_devices()
