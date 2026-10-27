"""Explicit modeled test-range inputs; not aircraft certification data."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SafetyRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class LandingArea(SafetyRecord):
    id: str = Field(min_length=1,max_length=80)
    x: float
    z: float
    radius_m: float = Field(default=8,ge=5,le=100)


class Obstacle(SafetyRecord):
    id: str = Field(min_length=1,max_length=80)
    x: float
    z: float
    radius_m: float = Field(ge=1,le=1000)
    height_m: float = Field(ge=0,le=1000)


class SafetyProfile(SafetyRecord):
    version: Literal['test_range_safety_v1'] = 'test_range_safety_v1'
    geofence_m: float = Field(default=2500,ge=100,le=10000)
    navigation_confidence: float | None = Field(default=1,ge=0,le=1)
    obstacle_sensor_available: bool | None = True
    fixture_refresh: bool = True
    input_freshness_s: float = Field(default=3,ge=.5,le=10)
    interface_health: dict[Literal['direct','satellite','mesh'],bool | None] = Field(default_factory=lambda:dict(direct=True,satellite=True,mesh=True))
    mission_criticality: Literal['routine','critical'] = 'routine'
    caution_battery: float = Field(default=.2,gt=0,le=1)
    critical_battery: float = Field(default=.1,gt=0,le=1)
    emergency_battery: float = Field(default=.05,ge=0,le=1)
    landing_areas: list[LandingArea] = Field(default_factory=list,max_length=100)
    obstacles: list[Obstacle] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def ordered(self):
        if not self.emergency_battery < self.critical_battery < self.caution_battery:
            raise ValueError('battery thresholds must satisfy emergency < critical < caution')
        if len({s.id for s in self.landing_areas})!=len(self.landing_areas): raise ValueError('landing area IDs must be unique')
        if len({s.id for s in self.obstacles})!=len(self.obstacles): raise ValueError('obstacle IDs must be unique')
        if any(abs(s.x)+s.radius_m>self.geofence_m or abs(s.z)+s.radius_m>self.geofence_m for s in self.landing_areas):
            raise ValueError('landing areas must lie inside the geofence')
        return self
