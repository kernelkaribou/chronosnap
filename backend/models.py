"""
Pydantic models for request/response validation
"""
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from enum import Enum


class StreamType(str, Enum):
    HTTP = "http"
    RTSP = "rtsp"
    DEVICE = "device"


class JobStatus(str, Enum):
    ACTIVE = "active"
    SLEEPING = "sleeping"  # Active but outside time window
    DISABLED = "disabled"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class VideoStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., max_length=2048, description="HTTP/RTSP stream URL or /dev/video* device path")
    stream_type: StreamType
    start_datetime: datetime
    end_datetime: Optional[datetime] = None
    interval_seconds: int = Field(..., ge=10, description="Capture interval in seconds")
    framerate: int = Field(default=30, gt=0)
    naming_pattern: Optional[str] = None
    time_window_enabled: bool = Field(default=False, description="Enable daily time window for captures")
    time_window_start: Optional[str] = Field(None, description="Start time in HH:MM format (e.g., '08:00')")
    time_window_end: Optional[str] = Field(None, description="End time in HH:MM format (e.g., '20:00')")
    warning_threshold: int = Field(default=3, ge=1, le=50, description="Consecutive failures before warning state")
    auto_build_enabled: bool = Field(default=False, description="Enable automatic timelapse builds")
    auto_build_interval_hours: int = Field(default=168, ge=1, le=8760, description="Hours between auto-builds")
    auto_build_fps: int = Field(default=30, gt=0, le=120, description="FPS for auto-built videos")
    auto_build_quality: str = Field(default="medium", pattern=r"^(low|medium|high|maximum)$")
    auto_build_resolution: str = Field(default="1920x1080", pattern=r"^\d+x\d+$")
    auto_build_text_overlay: Optional[str] = None  # JSON string of TextOverlayConfig
    capture_quality: str = Field(default="maximum", pattern=r"^(maximum|high|medium|low)$", description="Capture image quality preset")
    capture_resolution: str = Field(default="native", pattern=r"^(native|\d+x\d+)$", description="Capture resolution: 'native' or 'WxH'")
    source_width: Optional[int] = None
    source_height: Optional[int] = None
    tag_ids: Optional[List[int]] = None
    
    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://', 'rtsp://', 'rtsps://', '/dev/video')):
            raise ValueError("URL must start with http://, https://, rtsp://, rtsps://, or /dev/video")
        return v
    
    @model_validator(mode='after')
    def validate_device_url(self):
        """Ensure /dev/video paths are only used with device stream type."""
        import re
        if self.url.startswith('/dev/video'):
            if self.stream_type != StreamType.DEVICE:
                raise ValueError("Device paths require stream_type 'device'")
            if not re.match(r'^/dev/video\d+$', self.url):
                raise ValueError("Device path must be /dev/videoN (e.g., /dev/video0)")
        elif self.stream_type == StreamType.DEVICE:
            raise ValueError("Device stream type requires a /dev/video* path")
        return self
    
    @model_validator(mode='after')
    def validate_dates(self):
        if self.end_datetime:
            # End date must be after start date
            if self.end_datetime <= self.start_datetime:
                raise ValueError("End date must be after start date")
            
            # End date must be at least start + interval
            min_end = self.start_datetime + timedelta(seconds=self.interval_seconds)
            if self.end_datetime < min_end:
                raise ValueError(f"End date must be at least {self.interval_seconds} seconds after start date")
            
            # End date must be in the future
            from .utils import get_now
            now = get_now()
            if self.end_datetime < now:
                raise ValueError("End date must be in the future")
        
        # Validate time window
        if self.time_window_enabled:
            if not self.time_window_start or not self.time_window_end:
                raise ValueError("Time window start and end times are required when time window is enabled")
            
            # Validate time format (HH:MM)
            import re
            time_pattern = re.compile(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$')
            if not time_pattern.match(self.time_window_start):
                raise ValueError("Time window start must be in HH:MM format (e.g., '08:00')")
            if not time_pattern.match(self.time_window_end):
                raise ValueError("Time window end must be in HH:MM format (e.g., '20:00')")
        
        return self


class JobUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    url: Optional[str] = Field(None, max_length=2048)
    stream_type: Optional[StreamType] = None
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    interval_seconds: Optional[int] = Field(None, ge=10)
    framerate: Optional[int] = Field(None, gt=0)
    status: Optional[JobStatus] = None
    time_window_enabled: Optional[bool] = None
    time_window_start: Optional[str] = None
    time_window_end: Optional[str] = None
    warning_threshold: Optional[int] = Field(None, ge=1, le=50)
    auto_build_enabled: Optional[bool] = None
    auto_build_interval_hours: Optional[int] = Field(None, ge=1, le=8760)
    auto_build_fps: Optional[int] = Field(None, gt=0, le=120)
    auto_build_quality: Optional[str] = Field(None, pattern=r"^(low|medium|high|maximum)$")
    auto_build_resolution: Optional[str] = Field(None, pattern=r"^\d+x\d+$")
    auto_build_text_overlay: Optional[str] = None  # JSON string of TextOverlayConfig
    capture_quality: Optional[str] = Field(None, pattern=r"^(maximum|high|medium|low)$")
    capture_resolution: Optional[str] = Field(None, pattern=r"^(native|\d+x\d+)$")
    tag_ids: Optional[List[int]] = None

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if v is not None and not v.startswith(('http://', 'https://', 'rtsp://', 'rtsps://', '/dev/video')):
            raise ValueError("URL must start with http://, https://, rtsp://, rtsps://, or /dev/video")
        return v

    @model_validator(mode='after')
    def validate_device_url(self):
        """Ensure /dev/video paths are only used with device stream type on partial updates."""
        import re
        if self.url is not None and self.url.startswith('/dev/video'):
            if self.stream_type is not None and self.stream_type != StreamType.DEVICE:
                raise ValueError("Device paths require stream_type 'device'")
            if not re.match(r'^/dev/video\d+$', self.url):
                raise ValueError("Device path must be /dev/videoN (e.g., /dev/video0)")
        elif self.stream_type == StreamType.DEVICE and self.url is not None:
            if not self.url.startswith('/dev/video'):
                raise ValueError("Device stream type requires a /dev/video* path")
        return self


class TagBrief(BaseModel):
    id: int
    name: str
    color: str


class JobResponse(BaseModel):
    id: int
    name: str
    url: str
    stream_type: str
    start_datetime: str
    end_datetime: Optional[str]
    interval_seconds: int
    framerate: int
    status: str
    capture_path: str
    naming_pattern: str
    capture_count: int
    warning_message: Optional[str] = None
    storage_size: int
    time_window_enabled: int = 0  # SQLite returns as int
    time_window_start: Optional[str] = None
    time_window_end: Optional[str] = None
    warning_threshold: int = 3
    auto_build_enabled: int = 0
    auto_build_interval_hours: int = 168
    auto_build_fps: int = 30
    auto_build_quality: str = "medium"
    auto_build_resolution: str = "1920x1080"
    auto_build_text_overlay: Optional[str] = None
    capture_quality: str = "maximum"
    capture_resolution: str = "native"
    source_width: Optional[int] = None
    source_height: Optional[int] = None
    last_auto_build_at: Optional[str] = None
    auto_build_in_progress: int = 0
    next_scheduled_capture_at: Optional[str] = None
    next_capture_at: Optional[str] = None
    next_auto_build_at: Optional[str] = None
    latest_capture: Optional[Dict[str, Any]] = None
    tags: List[TagBrief] = []
    created_at: str
    updated_at: str


class CaptureResponse(BaseModel):
    id: int
    job_id: int
    job_name: Optional[str] = None
    file_path: str
    file_size: int
    captured_at: str
    is_favorite: bool = False
    thumbnail_path: Optional[str] = None
    has_thumbnail: bool = False
    tags: List[TagBrief] = []


class CaptureListResponse(BaseModel):
    captures: List[CaptureResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class CaptureDeleteRequest(BaseModel):
    capture_ids: List[int]


class TextOverlayConfig(BaseModel):
    enabled: bool = False
    text: str = ""
    font: str = "DejaVu Sans"
    font_size: int = Field(default=5, ge=1, le=20, description="Font size as percentage of image height")
    bold: bool = False
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    color_opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    position: str = Field(default="bottom-left", pattern=r"^(top-left|top-center|top-right|middle-left|middle-center|middle-right|bottom-left|bottom-center|bottom-right)$")
    background: bool = True
    background_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    background_opacity: float = Field(default=0.5, ge=0.0, le=1.0)


class VideoCreate(BaseModel):
    job_id: int
    name: str
    resolution: str = Field(default="1920x1080", pattern=r"^\d+x\d+$")
    framerate: int = Field(default=30, gt=0)
    quality: str = Field(default="high", pattern=r"^(low|medium|high|maximum)$")
    start_capture_id: Optional[int] = None
    end_capture_id: Optional[int] = None
    start_time: Optional[str] = None  # ISO datetime string
    end_time: Optional[str] = None  # ISO datetime string
    text_overlay: Optional[TextOverlayConfig] = None
    tag_ids: Optional[List[int]] = None


class VideoResponse(BaseModel):
    id: int
    job_id: Optional[int] = None
    job_name: Optional[str] = None
    name: str
    file_path: str
    file_size: int
    resolution: str
    framerate: int
    quality: str
    start_capture_id: Optional[int]
    end_capture_id: Optional[int]
    start_time: Optional[str]
    end_time: Optional[str]
    total_frames: int
    duration_seconds: float
    status: str
    progress: float
    created_at: str
    completed_at: Optional[str]
    thumbnail_path: Optional[str] = None
    build_source: str = "manual"
    is_favorite: bool = False
    text_overlay: Optional[str] = None
    tags: List[TagBrief] = []
    share_token: Optional[str] = None


class TestUrlResponse(BaseModel):
    success: bool
    message: str
    image_data: Optional[str] = None  # Base64 encoded image
    image_size: Optional[int] = None
    source_width: Optional[int] = None
    source_height: Optional[int] = None


class DeviceInfo(BaseModel):
    path: str
    name: str
    driver: str = "v4l2"


class DurationCalculation(BaseModel):
    fps: int
    duration_seconds: float
    duration_formatted: str


class DurationEstimate(BaseModel):
    captures: int
    calculations: List[DurationCalculation]


class MaintenanceResult(BaseModel):
    job_id: int
    job_name: str
    total_captures: int
    missing_files: List[Dict[str, Any]]
    missing_count: int
    orphaned_files: List[Dict[str, Any]]
    orphaned_count: int
    existing_count: int
    total_size_recovered: int


class MaintenanceCleanup(BaseModel):
    capture_ids: List[int]


class MaintenanceImport(BaseModel):
    orphaned_files: List[Dict[str, Any]]


class DirectoryScanRequest(BaseModel):
    directory: str


class DirectoryImportRequest(BaseModel):
    name: str
    directory: str
    url: Optional[str] = None
    stream_type: str = "rtsp"
    interval_seconds: int = Field(default=60, gt=0)
