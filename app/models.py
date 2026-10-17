from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class BatteryUnit(BaseModel):
    id: int
    name: str = "Акумулятор"
    soc: float = Field(default=0.0, description="Рівень заряду у відсотках (0-100%)")
    voltage: float = Field(default=0.0, description="Напруга (В)")
    current: float = Field(default=0.0, description="Струм (А)")
    power: float = Field(default=0.0, description="Потужність (Вт)")
    temperature: float = Field(default=25.0, description="Температура (°C)")
    soh: float = Field(default=100.0, description="Здоров'я акумулятора SOH (%)")
    status: str = Field(default="standby", description="charging / discharging / standby")


class BatterySystem(BaseModel):
    total_soc: float = Field(default=0.0, description="Середній/загальний SOC (%)")
    total_power: float = Field(default=0.0, description="Сумарна потужність (Вт): >0 заряд, <0 розряд")
    voltage: float = Field(default=0.0, description="Загальна напруга шини DC (В)")
    current: float = Field(default=0.0, description="Загальний струм (А)")
    status: str = Field(default="standby", description="charging / discharging / standby")
    daily_charge: float = Field(default=0.0, description="Заряджено за день (кВт⋅год)")
    daily_discharge: float = Field(default=0.0, description="Розряджено за день (кВт⋅год)")
    estimated_remaining_hours: Optional[float] = Field(default=None, description="Орієнтовний час до повного розряду/заряду (год)")
    batteries: List[BatteryUnit] = Field(default_factory=list)


class SolarSystem(BaseModel):
    pv1_power: float = Field(default=0.0, description="Потужність стрінгу 1 (Вт)")
    pv1_voltage: float = Field(default=0.0, description="Напруга стрінгу 1 (В)")
    pv1_current: float = Field(default=0.0, description="Струм стрінгу 1 (А)")

    pv2_power: float = Field(default=0.0, description="Потужність стрінгу 2 (Вт)")
    pv2_voltage: float = Field(default=0.0, description="Напруга стрінгу 2 (В)")
    pv2_current: float = Field(default=0.0, description="Струм стрінгу 2 (А)")

    total_power: float = Field(default=0.0, description="Сумарна генерація панелей (Вт)")
    daily_energy: float = Field(default=0.0, description="Згенеровано за день (кВт⋅год)")
    total_energy: float = Field(default=0.0, description="Загальна генерація за весь час (кВт⋅год)")


class GridSystem(BaseModel):
    is_online: bool = Field(default=True, description="Наявність напруги в міській мережі 220В")
    power: float = Field(default=0.0, description="Потужність мережі (Вт): >0 імпорт, <0 експорт")
    voltage: float = Field(default=230.0, description="Напруга в мережі (В)")
    frequency: float = Field(default=50.0, description="Частота струму (Гц)")
    daily_import: float = Field(default=0.0, description="Спожито з мережі за день (кВт⋅год)")
    daily_export: float = Field(default=0.0, description="Віддано в мережу за день (кВт⋅год)")


class LoadSystem(BaseModel):
    power: float = Field(default=0.0, description="Поточне споживання будинку (Вт)")
    daily_energy: float = Field(default=0.0, description="Спожито будинком за день (кВт⋅год)")


class InverterStatus(BaseModel):
    model: str = Field(default="Deye Hybrid 1-Phase")
    serial_number: str = Field(default="DEYE-1P-5K")
    work_mode: str = Field(default="Self-Use")
    temperature: float = Field(default=35.0, description="Температура інвертора (°C)")
    ac_temp: float = Field(default=34.0)
    fault_code: int = Field(default=0)
    fault_message: str = Field(default="Normal")


class SystemTelemetry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    source: str = Field(default="simulator", description="solarman_api, local_push, simulator")
    solar: SolarSystem = Field(default_factory=SolarSystem)
    battery: BatterySystem = Field(default_factory=BatterySystem)
    grid: GridSystem = Field(default_factory=GridSystem)
    load: LoadSystem = Field(default_factory=LoadSystem)
    inverter: InverterStatus = Field(default_factory=InverterStatus)


class TelemetryHistoryPoint(BaseModel):
    time: str
    solar_power: float
    battery_power: float
    load_power: float
    grid_power: float
    battery_soc: float
