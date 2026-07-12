"""
<plugin key="FullyKiosk" name="Fully Kiosk plugin" author="MadPatrick" version="1.1.1" wikilink="https://www.fully-kiosk.com/" externallink="https://github.com/MadPatrick/domoticz_fullykiosk">
    <description>
        <h2>Fully Kiosk Browser</h2>
        <p><strong>Version:</strong> 1.1.1</p>
        <p>Controls and monitors a tablet running Fully Kiosk Browser through its Remote Admin API.</p>
        <h3>Features</h3>
        <ul>
            <li>Screen, screensaver, motion sensor and brightness controls.</li>
            <li>Battery level and charging status.</li>
            <li>Loads the configured start URL from Domoticz.</li>
            <li>Optional charger control through an external Domoticz switch, including battery thresholds and a safety backup.</li>
        </ul>
        <h3>Configuration</h3>
        <p>Enter the tablet connection details. Leave the charger switch ID empty to disable charge control.</p>
    </description>
    <params>
        <param field="Address" label="Tablet IP" width="200px" required="true" default="192.168.1.200"/>
        <param field="Port" label="Port" width="100px" required="true" default="2323"/>
        <param field="Username" label="Username" width="150px"/>
        <param field="Password" label="Password" width="150px" password="true"/>
        <param field="Mode1" label="Refresh Interval (sec)" width="100px" required="true" default="60"/>
        <param field="Mode2" label="Charger switch ID" width="100px" required="false" default=""/>
        <param field="Mode3" label="Domoticz Host" width="150px" required="false" default="127.0.0.1"/>
        <param field="Mode4" label="Domoticz Port" width="100px" required="false" default="8080"/>
        <param field="Mode6" label="Debug logging" width="100px" default="False">
            <options>
                <option label="Off" value="False" default="true"/>
                <option label="On" value="True" />
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import datetime
import random
import requests
import time

# ---------------------------
# Unit constants
# ---------------------------
UNIT_SCREEN = 1
UNIT_SCREENSAVER = 2
UNIT_BATTERY = 3
UNIT_CHARGING = 4
UNIT_MOTION = 5
UNIT_LOADURL = 6
UNIT_BRIGHTNESS = 7

HARD_MIN_BATTERY = 15
HARD_MAX_BATTERY = 95
CHARGE_BACKUP_DELAY_SECONDS = 16 * 60 * 60

class BasePlugin:
    def __init__(self):
        self.base_url = ""
        self.port = 2323
        self.username = ""
        self.password = ""
        self.devices_created = False
        self.debug = False
        self.heartbeat_interval = 10
        self.last_full_refresh = 0
        self.full_refresh_interval = 300
        self.connected = None  # None = unknown, True = connected, False = error
        self.connection_error_delay = 60
        self.first_failure_time = None
        self.connection_error_logged = False
        self.last_error_type = None
        self.domoticz_api_host = "127.0.0.1"
        self.domoticz_api_port = "8080"
        self.charger_device_idx = 0
        self.charger_state = None
        self.last_charger_off_time = None
        self.charge_stop_target = random.randint(80, 90)
        self.charge_start_target = random.randint(25, 30)
        self.previous_charge_status = ""
        self.charger_api_error_logged = False
        self.imageID = 0

    def _load_device_icon(self):
        _IMAGE = "Fully"
        creating_new_icon = _IMAGE not in Images
        try:
            Domoticz.Image(f"{_IMAGE}.zip").Create()
        except Exception as e:
            Domoticz.Error(f"Unable to load icon pack '{_IMAGE}.zip': {e}")
            return
        if _IMAGE in Images:
            self.imageID = Images[_IMAGE].ID
            Domoticz.Log("Icons created and loaded." if creating_new_icon else
                         f"Icons found in database (ImageID={self.imageID}).")
        else:
            Domoticz.Error(f"Unable to load icon pack '{_IMAGE}.zip'")

    def _apply_device_icon(self):
        if not self.imageID:
            return
        for device in Devices.values():
            if device.Image != self.imageID:
                device.Update(nValue=device.nValue, sValue=device.sValue, Image=self.imageID)

    # ---------------------------
    # Logging
    # ---------------------------
    def log(self, message):
        if self.debug:
            Domoticz.Log(f"DEBUG: {message}")

    # ---------------------------
    # Plugin start
    # ---------------------------
    def onStart(self):
        Domoticz.Log(f"Starting Plugin version {Parameters['Version']}")

        self._load_device_icon()

        # Parameters
        self.base_url = Parameters["Address"]
        self.port = int(Parameters.get("Port", 2323))
        self.username = Parameters.get("Username", "")
        self.password = Parameters.get("Password", "")
        self.debug = Parameters.get("Mode6", "false").lower() == "true"
        self.domoticz_api_host = (Parameters.get("Mode3", "127.0.0.1") or "127.0.0.1").strip()
        self.domoticz_api_port = (Parameters.get("Mode4", "8080") or "8080").strip()
        try:
            self.charger_device_idx = int(Parameters.get("Mode2", "0") or 0)
        except Exception:
            self.charger_device_idx = 0

        # Refresh interval
        try:
            self.full_refresh_interval = max(1, int(Parameters.get("Mode1", 300)))
        except Exception:
            self.full_refresh_interval = 300
        Domoticz.Log(f"Polling interval set to {self.full_refresh_interval} seconds")

        if self.charger_device_idx > 0:
            Domoticz.Log(
                f"Charge control enabled for switch ID {self.charger_device_idx} "
                f"(start {self.charge_start_target}%, stop {self.charge_stop_target}%, "
                f"Domoticz API {self.domoticz_api_host}:{self.domoticz_api_port})"
            )
        else:
            Domoticz.Log("Charge control disabled: configure Charger switch ID in Mode2")

        # Short heartbeat
        Domoticz.Heartbeat(self.heartbeat_interval)
        self.log_startup_battery()

        # Create devices
        if not self.devices_created:
            created_devices = []

            if UNIT_SCREEN not in Devices:
                Domoticz.Device(Name="Screen", Unit=UNIT_SCREEN, TypeName="Switch", Used=1, Image=self.imageID).Create()
                created_devices.append("Screen")
            if UNIT_SCREENSAVER not in Devices:
                Domoticz.Device(Name="Screensaver", Unit=UNIT_SCREENSAVER, TypeName="Switch", Used=1, Image=self.imageID).Create()
                created_devices.append("Screensaver")
            if UNIT_BATTERY not in Devices:
                Domoticz.Device(Name="Battery", Unit=UNIT_BATTERY, Type=243, Subtype=6, Used=1, Image=self.imageID).Create()
                created_devices.append("Battery")
            if UNIT_CHARGING not in Devices:
                Domoticz.Device(Name="Charging", Unit=UNIT_CHARGING, TypeName="Switch", Used=1, Image=self.imageID).Create()
                created_devices.append("Charging")
            if UNIT_MOTION not in Devices:
                Domoticz.Device(Name="Motion Sensor", Unit=UNIT_MOTION, TypeName="Switch", Used=1, Image=self.imageID).Create()
                created_devices.append("Motion Sensor")
            if UNIT_LOADURL not in Devices:
                Domoticz.Device(Name="Load Start URL", Unit=UNIT_LOADURL, Type=244, Switchtype=9, Subtype=73, Used=1, Image=self.imageID).Create()
                created_devices.append("Load Start URL")
            if UNIT_BRIGHTNESS not in Devices:
                Domoticz.Device(Name="Brightness", Unit=UNIT_BRIGHTNESS, TypeName="Dimmer", Used=1, Image=self.imageID).Create()
                created_devices.append("Brightness")

            self.devices_created = True
            self._apply_device_icon()

            if created_devices:
                Domoticz.Log(f"Devices created: {', '.join(created_devices)}")

    # ---------------------------
    # API Call
    # ---------------------------
    def api_call(self, cmd, extra_params=None):
        params = {"cmd": cmd, "password": self.password}
        if self.username:
            params["username"] = self.username
        if extra_params:
            params.update(extra_params)
        url = f"http://{self.base_url}:{self.port}"
        try:
            # ---> HIER IS HET WACHTWOORD GEMASKEERD <---
            safe_params = dict(params)
            if "password" in safe_params and safe_params["password"]:
                safe_params["password"] = "********"
            self.log(f"API call: {url} params={safe_params}")
            # -------------------------------------------

            r = requests.get(url, params=params, timeout=5)
            r.raise_for_status()

            # Connection succeeded
            if self.connection_error_logged:
                Domoticz.Log("Connection restored")
            self.connected = True
            self.first_failure_time = None
            self.connection_error_logged = False
            self.last_error_type = None

            try:
                data = r.json()
                self.log(f"API response: {data}")
                return data
            except Exception:
                self.log(f"API returned non-JSON: {r.text}")
                return None
        except Exception as e:
            msg = str(e)
            if "No route to host" in msg:
                short = "No route to host"
            elif "Connection refused" in msg:
                short = "Connection refused"
            elif "timed out" in msg.lower():
                short = "Connection timed out"
            else:
                short = "Connection failed"

            now = time.time()
            if self.first_failure_time is None:
                self.first_failure_time = now
                self.log(f"{short} (waiting {self.connection_error_delay}s before error)")

            # Log only after 60 seconds of continuous failures
            if now - self.first_failure_time >= self.connection_error_delay:
                # Log again if error type changes while connection is still failing
                if (not self.connection_error_logged) or (self.last_error_type != short):
                    Domoticz.Error(f"{short} (Connection failed to Tablet)")
                self.connection_error_logged = True
                self.last_error_type = short
            self.connected = False
            return None

    def log_startup_battery(self):
        info = self.api_call("getDeviceInfo", {"type":"json"})
        if not info:
            return

        try:
            battery_level = int(info.get("batteryLevel", 0))
            battery_level = max(0, min(100, battery_level))
            Domoticz.Log(f"Current battery level: {battery_level}%")
        except Exception:
            Domoticz.Error(f"Invalid battery level received from Fully Kiosk at startup: {info.get('batteryLevel')}")

    # ---------------------------
    # Domoticz charger switch
    # ---------------------------
    def domoticz_api_call(self, params):
        url = f"http://{self.domoticz_api_host}:{self.domoticz_api_port}/json.htm"
        try:
            self.log(f"Domoticz API call: {url} params={params}")
            r = requests.get(url, params=params, timeout=5)
            r.raise_for_status()
            data = r.json()
            self.charger_api_error_logged = False
            return data
        except requests.exceptions.HTTPError as e:
            response = getattr(e, "response", None)
            status_code = response.status_code if response is not None else "unknown"
            if not self.charger_api_error_logged:
                Domoticz.Error(
                    f"Domoticz API error for charger switch ID {self.charger_device_idx}: "
                    f"HTTP {status_code} at {url}. Check Domoticz API Host/Port and API access."
                )
            self.charger_api_error_logged = True
            return None
        except Exception as e:
            if not self.charger_api_error_logged:
                Domoticz.Error(f"Domoticz API error for charger switch ID {self.charger_device_idx}: {e}")
            self.charger_api_error_logged = True
            return None

    def get_charger_device_info(self):
        if self.charger_device_idx <= 0:
            return None

        data = self.domoticz_api_call({
            "type": "command",
            "param": "getdevices",
            "rid": str(self.charger_device_idx)
        })
        if not data:
            return None

        result = data.get("result") or []
        if not result:
            self.log(f"No Domoticz device found for charger switch ID {self.charger_device_idx}")
            return None
        return result[0]

    def parse_domoticz_datetime(self, value):
        if not value:
            return None

        value = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
            try:
                return datetime.datetime.strptime(value, fmt)
            except Exception:
                pass
        return None

    def charger_state_from_info(self, info):
        if not info:
            return None

        state = str(info.get("Status") or info.get("Data") or "").strip().lower()
        if state == "on":
            return "On"
        if state == "off":
            return "Off"

        nvalue = str(info.get("nValue", "")).strip()
        if nvalue == "1":
            return "On"
        if nvalue == "0":
            return "Off"

        return None

    def read_charger_switch(self):
        info = self.get_charger_device_info()
        state = self.charger_state_from_info(info)

        if state:
            self.charger_state = state
            if state == "Off":
                last_update = self.parse_domoticz_datetime(info.get("LastUpdate")) if info else None
                if last_update:
                    self.last_charger_off_time = last_update.timestamp()
                elif self.last_charger_off_time is None:
                    self.last_charger_off_time = time.time()
            else:
                self.last_charger_off_time = None

        return state, info

    def charger_off_age_seconds(self, info):
        if info:
            last_update = self.parse_domoticz_datetime(info.get("LastUpdate"))
            if last_update:
                return max(0, (datetime.datetime.now() - last_update).total_seconds())

        if self.last_charger_off_time is not None:
            return max(0, time.time() - self.last_charger_off_time)

        return None

    def set_charger_switch(self, command, reason=None, log_action=True):
        if self.charger_device_idx <= 0:
            return False

        data = self.domoticz_api_call({
            "type": "command",
            "param": "switchlight",
            "idx": str(self.charger_device_idx),
            "switchcmd": command
        })
        if not data:
            return False

        status = str(data.get("status", "OK")).upper()
        if status != "OK":
            Domoticz.Error(f"Unable to switch charger ID {self.charger_device_idx} to {command}: {data}")
            return False

        self.charger_state = command
        if command == "Off":
            self.last_charger_off_time = time.time()
        else:
            self.last_charger_off_time = None

        if log_action and reason:
            Domoticz.Log(f"{reason}: Switch ID {self.charger_device_idx} -> {command}")
        return True

    def start_charging(self, battery_level, reason):
        if not self.set_charger_switch("On", log_action=False):
            return False

        self.charge_stop_target = random.randint(80, 90)
        self.charge_start_target = random.randint(20, 30)
        Domoticz.Log(
            f"Charging started at {battery_level}% ({reason}); "
            f"charger switch ID {self.charger_device_idx} -> On; "
            f"stop target {self.charge_stop_target}%."
        )
        self.previous_charge_status = "Charging"
        return True

    def stop_charging(self, battery_level, reason):
        next_start_target = random.randint(25, 30)
        if not self.set_charger_switch("Off", log_action=False):
            return False

        self.charge_start_target = next_start_target
        Domoticz.Log(
            f"Charging stopped at {battery_level}% ({reason}); "
            f"charger switch ID {self.charger_device_idx} -> Off; "
            f"next start at {self.charge_start_target}%."
        )
        self.previous_charge_status = "Discharging"
        return True

    def log_charge_status(self, battery_level, charger_state):
        current_status = "Charging" if charger_state == "On" else "Discharging"
        if current_status == "Charging":
            status_string = (
                f"Battery {battery_level}%; charging "
                f"(stop target {self.charge_stop_target}%)"
            )
        else:
            status_string = (
                f"Battery {battery_level}%; discharging "
                f"(starts charging at {self.charge_start_target}%)"
            )

        if current_status != self.previous_charge_status:
            Domoticz.Log(status_string)
            self.previous_charge_status = current_status

    def handle_charge_control(self, battery_level):
        if self.charger_device_idx <= 0:
            return

        charger_state, _ = self.read_charger_switch()
        if charger_state is None:
            charger_state = self.charger_state

        if charger_state not in ("On", "Off"):
            self.log("Charge control skipped: charger switch state is unknown")
            return

        if battery_level <= HARD_MIN_BATTERY and charger_state == "Off":
            if self.start_charging(
                battery_level,
                f"hard minimum {HARD_MIN_BATTERY}% reached"
            ):
                charger_state = "On"

        if battery_level >= HARD_MAX_BATTERY and charger_state == "On":
            if self.stop_charging(battery_level, f"hard maximum {HARD_MAX_BATTERY}% reached"):
                return

        if battery_level <= self.charge_start_target and charger_state == "Off":
            if self.start_charging(battery_level, f"start threshold {self.charge_start_target}% reached"):
                charger_state = "On"

        if charger_state == "On":
            stop_reason = None
            if battery_level >= 100:
                stop_reason = "fully charged (100%)"
            elif battery_level >= self.charge_stop_target:
                stop_reason = f"stop target {self.charge_stop_target}% reached"

            if stop_reason and self.stop_charging(battery_level, stop_reason):
                return

        self.log_charge_status(battery_level, charger_state)

    def handle_charger_backup(self):
        if self.charger_device_idx <= 0:
            return

        charger_state, info = self.read_charger_switch()
        if charger_state == "On":
            self.log("Tablet unreachable; charger switch is already On")
            return

        if charger_state != "Off":
            self.log("Tablet unreachable; charger backup skipped because switch state is unknown")
            return

        off_age = self.charger_off_age_seconds(info)
        if off_age is None:
            self.log("Tablet unreachable; charger backup skipped because off age is unknown")
            return

        if off_age >= CHARGE_BACKUP_DELAY_SECONDS:
            Domoticz.Log(
                f"Tablet unreachable and charger switch ID {self.charger_device_idx} "
                f"has been Off for {off_age / 3600:.1f} hours; backup switches it On"
            )
            self.set_charger_switch("On", "Backup charging")
        else:
            self.log(
                f"Tablet unreachable; charger switch ID {self.charger_device_idx} "
                f"has been Off for {off_age / 3600:.1f} hours"
            )

    # ---------------------------
    # Commands
    # ---------------------------
    def onCommand(self, Unit, Command, Level, Color):
        if Unit == UNIT_SCREEN:
            cmd = "screenOn" if Command == "On" else "screenOff"
            self.api_call(cmd)
            self.log(f"Screen command sent: {cmd}")
            if UNIT_SCREEN in Devices:
                Devices[UNIT_SCREEN].Update(nValue=1 if Command == "On" else 0, sValue=Command)
        elif Unit == UNIT_SCREENSAVER:
            cmd = "startScreensaver" if Command == "On" else "stopScreensaver"
            self.api_call(cmd)
            self.log(f"Screensaver command sent: {cmd}")
            if UNIT_SCREENSAVER in Devices:
                Devices[UNIT_SCREENSAVER].Update(nValue=1 if Command == "On" else 0, sValue=Command)
        elif Unit == UNIT_MOTION:
            enabled = Command == "On"
            self.api_call("setConfig", {"key":"motionDetectionEnabled","value":"true" if enabled else "false"})
            self.log(f"Motion sensor command sent: {enabled}")
            if UNIT_MOTION in Devices:
                Devices[UNIT_MOTION].Update(nValue=1 if enabled else 0, sValue=Command)
        elif Unit == UNIT_LOADURL:
            start_url = self.api_call("getDeviceInfo", {"type":"json"})
            if start_url:
                start_url = start_url.get("startUrl", "")
                if start_url:
                    self.api_call("loadUrl", {"url": start_url})
                    Domoticz.Log(f"Load Start URL command sent: {start_url}")
        elif Unit == UNIT_BRIGHTNESS and Command == "Set Level":
            level = int(Level)
            self.api_call("setScreenBrightness", {"value": str(level)})
            if UNIT_BRIGHTNESS in Devices:
                Devices[UNIT_BRIGHTNESS].Update(nValue=2 if level > 0 else 0, sValue=str(level))
            self.log(f"Set brightness to: {level}")

    # ---------------------------
    # Heartbeat
    # ---------------------------
    def onHeartbeat(self):
        now = time.time()
        if now - self.last_full_refresh < self.full_refresh_interval:
            return
        self.last_full_refresh = now

        try:
            info = self.api_call("getDeviceInfo", {"type":"json"})
            if not info:
                self.log("No data from Fully Kiosk received.")
                self.handle_charger_backup()
                return

            try:
                battery_level = int(info.get("batteryLevel", 0))
                battery_level = max(0, min(100, battery_level))
            except Exception:
                battery_level = None
                Domoticz.Error(f"Invalid battery level received from Fully Kiosk: {info.get('batteryLevel')}")

            # Screen
            if UNIT_SCREEN in Devices:
                screen_on = info.get("screenOn", False)
                Devices[UNIT_SCREEN].Update(nValue=1 if screen_on else 0, sValue="On" if screen_on else "Off")
                self.log(f"Screen: {screen_on}")

            # Screensaver
            if UNIT_SCREENSAVER in Devices:
                screensaver_on = info.get("isInScreensaver", False)
                Devices[UNIT_SCREENSAVER].Update(nValue=1 if screensaver_on else 0, sValue="On" if screensaver_on else "Off")
                self.log(f"Screensaver: {screensaver_on}")

            # Battery
            if UNIT_BATTERY in Devices and battery_level is not None:
                Devices[UNIT_BATTERY].Update(nValue=battery_level, sValue=str(battery_level))
                self.log(f"Battery: {battery_level}%")

            # Charging
            if UNIT_CHARGING in Devices:
                charging = info.get("isPlugged", False)
                Devices[UNIT_CHARGING].Update(nValue=1 if charging else 0, sValue="On" if charging else "Off")
                self.log(f"Charging: {charging}")

            # Motion
            if UNIT_MOTION in Devices:
                motion_on = info.get("motionDetectionEnabled", info.get("motionDetectorStarted", False))
                Devices[UNIT_MOTION].Update(nValue=1 if motion_on else 0, sValue="On" if motion_on else "Off")
                self.log(f"Motion: {motion_on}")

            # Brightness
            if UNIT_BRIGHTNESS in Devices:
                brightness = int(info.get("screenBrightness", 0))
                brightness = max(0, min(100, brightness))
                Devices[UNIT_BRIGHTNESS].Update(nValue=2 if brightness > 0 else 0, sValue=str(brightness))
                self.log(f"Brightness: {brightness}")

            if battery_level is not None:
                self.handle_charge_control(battery_level)

        except Exception as e:
            Domoticz.Error(f"Heartbeat error: {e}")


# ---------------------------
# Globale plugin instantie
# ---------------------------
global _plugin
_plugin = BasePlugin()

def onStart():
    _plugin.onStart()

def onStop():
    Domoticz.Log("Plugin stopped")

def onHeartbeat():
    _plugin.onHeartbeat()

def onCommand(Unit, Command, Level, Color):
    _plugin.onCommand(Unit, Command, Level, Color)
