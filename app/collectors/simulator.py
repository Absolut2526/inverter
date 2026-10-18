import math
import random
from datetime import datetime
from app.models import (
    BatterySystem,
    BatteryUnit,
    GridSystem,
    InverterStatus,
    LoadSystem,
    SolarSystem,
    SystemTelemetry,
)


class TelemetrySimulator:
    """Generates physically realistic telemetry for Deye 1-phase hybrid inverter with 2 batteries."""

    def __init__(self):
        self.batt1_soc = 82.0
        self.batt2_soc = 80.5
        self.daily_solar_kwh = 12.4
        self.daily_load_kwh = 7.8
        self.daily_import_kwh = 1.2
        self.daily_export_kwh = 4.5
        self.daily_charge_kwh = 6.8
        self.daily_discharge_kwh = 3.9
        self.grid_online = True
        self.manual_blackout = False

    def toggle_grid(self, online: bool = None) -> bool:
        if online is None:
            self.manual_blackout = not self.manual_blackout
        else:
            self.manual_blackout = not online
        self.grid_online = not self.manual_blackout
        return self.grid_online

    def generate(self) -> SystemTelemetry:
        now = datetime.now()
        hour = now.hour + now.minute / 60.0

        # Solar bell curve between 6:00 and 19:00
        if 6.0 <= hour <= 19.5:
            # Normalized peak at 13:00
            solar_factor = math.sin((hour - 6.0) / (19.5 - 6.0) * math.pi)
            peak_power = 4200.0  # Max capacity 4.2 kW
            noise = random.uniform(0.92, 1.05)
            pv_total = max(0.0, peak_power * solar_factor * noise)
        else:
            pv_total = 0.0

        # Split across 2 strings (PV1 and PV2)
        pv1_ratio = 0.52 + random.uniform(-0.03, 0.03)
        pv1_power = round(pv_total * pv1_ratio, 1)
        pv2_power = round(max(0.0, pv_total - pv1_power), 1)

        pv1_volts = round(340.0 + random.uniform(-5, 5) if pv1_power > 0 else 0.0, 1)
        pv1_amps = round(pv1_power / pv1_volts if pv1_volts > 0 else 0.0, 2)
        pv2_volts = round(350.0 + random.uniform(-5, 5) if pv2_power > 0 else 0.0, 1)
        pv2_amps = round(pv2_power / pv2_volts if pv2_volts > 0 else 0.0, 2)

        # House consumption (Load)
        # Typical morning/evening spikes
        base_load = 450.0
        if 7.5 <= hour <= 9.5 or 18.0 <= hour <= 22.0:
            base_load += 800.0
        load_noise = random.uniform(-80, 150)
        load_power = round(max(250.0, base_load + load_noise), 1)

        # Net balance: Solar vs Load
        power_balance = pv_total - load_power

        # Battery behaviour
        # 2 x 5.12 kWh batteries = 10.24 kWh total
        total_capacity_wh = 10240.0
        current_soc = (self.batt1_soc + self.batt2_soc) / 2.0

        grid_active = self.grid_online and not self.manual_blackout

        is_force_charging = False
        try:
            from app.collectors.charge_controller import charge_controller
            is_force_charging = bool(
                charge_controller.is_force_charging
                and grid_active
                and current_soc < getattr(charge_controller, "force_charge_target_soc", 100.0)
            )
        except Exception:
            pass

        if is_force_charging:
            battery_power = 2800.0  # Active charging from grid at ~2.8 kW
            grid_power = max(0.0, load_power + battery_power - pv_total)
            battery_status = "charging"
        elif power_balance > 0:
            # Surplus power charges the battery
            if current_soc < 99.0:
                battery_power = min(power_balance, 3000.0)  # Max charge rate ~3kW
                grid_power = -(power_balance - battery_power) if grid_active else 0.0  # export surplus
                battery_status = "charging"
            else:
                battery_power = 0.0
                grid_power = -power_balance if grid_active else 0.0  # export all to grid
                battery_status = "standby"
        else:
            # Deficit: need power
            deficit = abs(power_balance)
            if current_soc > 15.0:
                # Discharge battery to cover load
                battery_power = -deficit
                grid_power = 0.0
                battery_status = "discharging"
            else:
                # Battery low!
                if grid_active:
                    battery_power = 0.0
                    grid_power = deficit  # take from grid
                    battery_status = "standby"
                else:
                    # Critical off-grid
                    battery_power = -deficit
                    grid_power = 0.0
                    battery_status = "discharging"

        # Update battery SOC slightly based on power flow
        delta_hours = 15.0 / 3600.0  # 15 seconds
        wh_delta = battery_power * delta_hours
        soc_delta = (wh_delta / total_capacity_wh) * 100.0

        self.batt1_soc = max(5.0, min(100.0, self.batt1_soc + soc_delta * 0.99))
        self.batt2_soc = max(5.0, min(100.0, self.batt2_soc + soc_delta * 1.01))
        avg_soc = round((self.batt1_soc + self.batt2_soc) / 2.0, 1)

        # Cumulative energy updates
        if pv_total > 0:
            self.daily_solar_kwh += (pv_total * delta_hours) / 1000.0
        self.daily_load_kwh += (load_power * delta_hours) / 1000.0
        if grid_power > 0:
            self.daily_import_kwh += (grid_power * delta_hours) / 1000.0
        elif grid_power < 0:
            self.daily_export_kwh += (abs(grid_power) * delta_hours) / 1000.0

        # DC Bus Voltage for 48V/51.2V LiFePO4
        batt_volts = round(48.0 + (avg_soc / 100.0) * 5.6 + random.uniform(-0.1, 0.1), 2)
        batt_amps = round(battery_power / batt_volts, 2)

        # Remaining hours estimate
        if battery_status == "discharging" and abs(battery_power) > 50:
            usable_wh = (avg_soc - 10.0) / 100.0 * total_capacity_wh
            est_hours = round(max(0.1, usable_wh / abs(battery_power)), 1)
        elif battery_status == "charging" and battery_power > 50:
            needed_wh = (100.0 - avg_soc) / 100.0 * total_capacity_wh
            est_hours = round(max(0.1, needed_wh / battery_power), 1)
        else:
            est_hours = None

        # Build battery units
        batt1_power = round(battery_power / 2.0, 1)
        batt2_power = round(battery_power / 2.0, 1)
        batt1_amps = round(batt_amps / 2.0, 2)
        batt2_amps = round(batt_amps / 2.0, 2)

        batt1 = BatteryUnit(
            id=1,
            name="Акумулятор 1 (Master)",
            soc=round(self.batt1_soc, 1),
            voltage=batt_volts,
            current=batt1_amps,
            power=batt1_power,
            temperature=round(24.5 + (0.5 if abs(battery_power) > 1000 else 0), 1),
            soh=99.0,
            status=battery_status,
        )

        batt2 = BatteryUnit(
            id=2,
            name="Акумулятор 2 (Slave)",
            soc=round(self.batt2_soc, 1),
            voltage=round(batt_volts - 0.05, 2),
            current=batt2_amps,
            power=batt2_power,
            temperature=round(25.0 + (0.7 if abs(battery_power) > 1000 else 0), 1),
            soh=98.5,
            status=battery_status,
        )

        return SystemTelemetry(
            timestamp=datetime.now().isoformat(),
            source="simulator",
            solar=SolarSystem(
                pv1_power=pv1_power,
                pv1_voltage=pv1_volts,
                pv1_current=pv1_amps,
                pv2_power=pv2_power,
                pv2_voltage=pv2_volts,
                pv2_current=pv2_amps,
                total_power=round(pv_total, 1),
                daily_energy=round(self.daily_solar_kwh, 2),
                total_energy=3420.5,
            ),
            battery=BatterySystem(
                total_soc=avg_soc,
                total_power=round(battery_power, 1),
                voltage=batt_volts,
                current=batt_amps,
                status=battery_status,
                daily_charge=round(self.daily_charge_kwh, 2),
                daily_discharge=round(self.daily_discharge_kwh, 2),
                estimated_remaining_hours=est_hours,
                batteries=[batt1, batt2],
            ),
            grid=GridSystem(
                is_online=grid_active,
                power=round(grid_power, 1),
                voltage=round(228.0 + random.uniform(-3, 3) if grid_active else 0.0, 1),
                frequency=round(50.0 + random.uniform(-0.05, 0.05) if grid_active else 0.0, 2),
                daily_import=round(self.daily_import_kwh, 2),
                daily_export=round(self.daily_export_kwh, 2),
            ),
            load=LoadSystem(
                power=load_power,
                daily_energy=round(self.daily_load_kwh, 2),
            ),
            inverter=InverterStatus(
                model="Deye SUN-5K-SG03LP1-EU",
                serial_number="DEYE2406129841",
                work_mode="Self-Use (Zero Export to CT)" if grid_active else "Backup EPS (Off-Grid)",
                temperature=round(34.2 + (pv_total / 2000.0) * 4.0, 1),
                ac_temp=round(33.5 + (load_power / 2000.0) * 3.5, 1),
                fault_code=0,
                fault_message="Нормальна робота",
            ),
        )
