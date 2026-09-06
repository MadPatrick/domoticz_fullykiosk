"""
<plugin key="FullyKiosk" name="Fully Kiosk plugin" author="MadPatrick" version="1.2.0" wikilink="https://www.fully-kiosk.com/" externallink="https://github.com/MadPatrick/domoticz_fullykiosk">
    <description>
        <h2>Fully Kiosk Browser</h2>
        <p><strong>Version:</strong> 1.2.0</p>
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
        <param field="Address" label="Tablet IP" width="200px" required="true" default="192.168.1.200">
            <description>
                <h4 style="margin:4px 0 6px 0;">Tablet connection</h4>
            </description>
        </param>
        <param field="Port" label="Port" type="number" min="1" max="65535" step="1" width="100px" required="true" default="2323"/>
        <param field="Username" label="Username" width="150px"/>
        <param field="Password" label="Password" width="150px" password="true"/>
        <param field="UseHTTPS" type="boolean" label="Use HTTPS" default="">
            <description>
                <br/>Connects to the tablet's own Remote Admin HTTPS listener (enable "Remote Administration via HTTPS" in Fully Kiosk). Uses the tablet's self-signed certificate, so certificate verification is skipped for this connection.
            </description>
        </param>
        <param field="RefreshInterval" type="number" label="Refresh interval (sec)" min="1" max="86400" step="1" width="100px" default="">
            <description>
                <h4 style="margin:14px 0 6px 0; border-top:1px solid #ccc; padding-top:8px;">Polling</h4>
            </description>
        </param>
        <param field="ChargerSwitchID" type="number" label="Charger switch ID" min="0" step="1" width="100px" required="false" default="">
            <description>
                <h4 style="margin:14px 0 6px 0; border-top:1px solid #ccc; padding-top:8px;">Charge control</h4>
                <br/>Leave empty or use 0 to disable charge control.
            </description>
        </param>
        <param field="DomoticzHost" label="Domoticz host" width="150px" required="false" default=""/>
        <param field="DomoticzPort" type="number" label="Domoticz port" min="1" max="65535" step="1" width="100px" required="false" default=""/>
        <param field="EnableDebug" type="boolean" label="Debug logging" default="">
            <description>
                <h4 style="margin:14px 0 6px 0; border-top:1px solid #ccc; padding-top:8px;">Logging</h4>
            </description>
        </param>
    </params>
</plugin>
"""

import Domoticz
import datetime
import random
import requests
import time
import threading
import queue
import warnings
from contextlib import contextmanager

try:
    from urllib3.exceptions import InsecureRequestWarning
except ImportError:  # pragma: no cover - urllib3 always ships with requests
    InsecureRequestWarning = Warning


@contextmanager
def _suppress_insecure_warning():
    """Locally suppress the InsecureRequestWarning for a single request to the
    tablet's own self-signed HTTPS listener, instead of disabling the
    warning process-wide for the whole interpreter."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=InsecureRequestWarning)
        yield

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

        # Reuse TCP connections instead of opening a new one per request
        self.http = requests.Session()

        self.use_https = False
        self.api_base_url = ""

        # The heartbeat fetch (tablet status + charge control) runs on a
        # background thread so a slow/unreachable tablet never blocks
        # Domoticz's single callback thread. The worker only calls api_call()/
        # domoticz_api_call()/handle_charge_control() (none of which touch
        # Devices[...] - charge control talks to Domoticz's own HTTP API, not
        # this plugin's Devices dict); Devices[...] updates happen in
        # _processHeartbeatResult(), on the main thread, from onHeartbeat.
        self._fetch_lock = threading.Lock()
        self._fetch_in_progress = False
        self._result_queue = queue.Queue()

    def _load_device_icon(self):
        # The zip file on disk keeps its historical short name, but the icon's
        # Base (in icons.txt, used as the Images dict key) must start with
        # this plugin's key ("FullyKiosk") - Domoticz only loads a plugin's
        # pre-existing custom icons into Images at startup when
        # Base LIKE '<PluginKey>%'. A Base that doesn't satisfy that (the
        # short "Fully" used before) means Images never contains it on
        # restart, so it gets silently recreated (and re-logged as "created")
        # every single time instead of being found.
        _ZIP_FILE = "Fully"
        _IMAGE = "FullyKiosk"
        existing_image = next(
            (image for name, image in Images.items()
             if str(name).casefold() == _IMAGE.casefold()),
            None,
        )
        if existing_image is not None:
            self.imageID = existing_image.ID
            Domoticz.Log(f"Icons found in database (ImageID={self.imageID}).")
            return

        try:
            Domoticz.Image(f"{_ZIP_FILE}.zip").Create()
        except Exception as e:
            Domoticz.Error(f"Unable to load icon pack '{_ZIP_FILE}.zip': {e}")
            return
        created_image = next(
            (image for name, image in Images.items()
             if str(name).casefold() == _IMAGE.casefold()),
            None,
        )
        if created_image is not None:
            self.imageID = created_image.ID
            Domoticz.Log("Icons created and loaded.")
        else:
            Domoticz.Error(f"Unable to load icon pack '{_ZIP_FILE}.zip'")

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

    def _read_int_parameter(self, field, default, minimum=None, maximum=None):
        raw = Parameters.get(field, "")
        if raw is None or str(raw).strip() == "":
            return default
        try:
            value = int(raw)
            if minimum is not None and value < minimum:
                raise ValueError
            if maximum is not None and value > maximum:
                raise ValueError
            return value
        except (TypeError, ValueError):
            Domoticz.Error(
                f"Invalid {field} value '{raw}'. Using default {default}."
            )
            return default

    def _read_migrated_parameter(self, field, legacy_field, default=""):
        """Read a named setting, falling back to its former Mode field.

        Empty defaults on the new settings make existing Domoticz hardware
        configurations continue to work until they are saved with the new
        field names.
        """
        raw = Parameters.get(field, "")
        if raw is None or str(raw).strip() == "":
            raw = Parameters.get(legacy_field, "")
        if raw is None or str(raw).strip() == "":
            return default
        return raw

    def _read_migrated_int_parameter(
        self, field, legacy_field, default, minimum=None, maximum=None
    ):
        raw = self._read_migrated_parameter(field, legacy_field, default)
        try:
            value = int(raw)
            if minimum is not None and value < minimum:
                raise ValueError
            if maximum is not None and value > maximum:
                raise ValueError
            return value
        except (TypeError, ValueError):
            Domoticz.Error(
                f"Invalid {field} value '{raw}'. Using default {default}."
            )
            return default

    def _read_migrated_boolean_parameter(self, field, legacy_field, default=False):
        raw = self._read_migrated_parameter(
            field, legacy_field, "true" if default else "false"
        )
        return str(raw).strip().lower() in ("true", "1", "yes", "on")

    # ---------------------------
    # Plugin start
    # ---------------------------
    def onStart(self):
        Domoticz.Log(f"Starting Plugin version {Parameters['Version']}")

        self._load_device_icon()

        # Parameters
        self.base_url = Parameters["Address"]
        self.port = self._read_int_parameter("Port", 2323, 1, 65535)
        self.username = Parameters.get("Username", "")
        self.password = Parameters.get("Password", "")
        self.use_https = self._read_migrated_boolean_parameter(
            "UseHTTPS", "Mode5"
        )
        scheme = "https" if self.use_https else "http"
        self.api_base_url = f"{scheme}://{self.base_url}:{self.port}"
        self.debug = self._read_migrated_boolean_parameter(
            "EnableDebug", "Mode6"
        )
        self.domoticz_api_host = str(
            self._read_migrated_parameter(
                "DomoticzHost", "Mode3", "127.0.0.1"
            )
        ).strip()
        self.domoticz_api_port = str(
            self._read_migrated_int_parameter(
                "DomoticzPort", "Mode4", 8080, 1, 65535
            )
        )
        self.charger_device_idx = self._read_migrated_int_parameter(
            "ChargerSwitchID", "Mode2", 0, 0
        )

        # Refresh interval
        self.full_refresh_interval = self._read_migrated_int_parameter(
            "RefreshInterval", "Mode1", 60, 1, 86400
        )
        Domoticz.Log(f"Polling interval set to {self.full_refresh_interval} seconds")

        if self.charger_device_idx > 0:
            Domoticz.Log(
                f"Charge control enabled for switch ID {self.charger_device_idx} "
                f"(start {self.charge_start_target}%, stop {self.charge_stop_target}%, "
                f"Domoticz API {self.domoticz_api_host}:{self.domoticz_api_port})"
            )
        else:
            Domoticz.Log("Charge control disabled: configure Charger switch ID")

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
        url = self.api_base_url
        try:
            # ---> HIER IS HET WACHTWOORD GEMASKEERD <---
            safe_params = dict(params)
            if "password" in safe_params and safe_params["password"]:
                safe_params["password"] = "********"
            self.log(f"API call: {url} params={safe_params}")
            # -------------------------------------------

            if self.use_https:
                # Tablet's own self-signed HTTPS listener - no CA to verify against.
                with _suppress_insecure_warning():
                    r = self.http.get(url, params=params, timeout=5, verify=False)
            else:
                r = self.http.get(url, params=params, timeout=5)
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
                # HTTP succeeded (2xx, checked above via raise_for_status()) but the
                # body wasn't valid JSON. That's still a successful round-trip to the
                # tablet - only a genuine connection/HTTP failure (handled in the
                # except block below) should be treated as "the command failed".
                # Returning None here would make callers that check
                # `if api_call(...) is not None` wrongly report failure.
                self.log(f"API returned non-JSON: {r.text}")
                return {"raw": r.text}
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
            r = self.http.get(url, params=params, timeout=5)
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
        self.charge_start_target = random.randint(25, 30)
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
        try:
            if Unit == UNIT_SCREEN:
                cmd = "screenOn" if Command == "On" else "screenOff"
                result = self.api_call(cmd)
                self.log(f"Screen command sent: {cmd}")
                if result is not None:
                    if UNIT_SCREEN in Devices:
                        Devices[UNIT_SCREEN].Update(nValue=1 if Command == "On" else 0, sValue=Command)
                else:
                    Domoticz.Error("Failed to set screen state on Fully Kiosk device")
            elif Unit == UNIT_SCREENSAVER:
                cmd = "startScreensaver" if Command == "On" else "stopScreensaver"
                result = self.api_call(cmd)
                self.log(f"Screensaver command sent: {cmd}")
                if result is not None:
                    if UNIT_SCREENSAVER in Devices:
                        Devices[UNIT_SCREENSAVER].Update(nValue=1 if Command == "On" else 0, sValue=Command)
                else:
                    Domoticz.Error("Failed to set screensaver state on Fully Kiosk device")
            elif Unit == UNIT_MOTION:
                enabled = Command == "On"
                result = self.api_call("setConfig", {"key":"motionDetectionEnabled","value":"true" if enabled else "false"})
                self.log(f"Motion sensor command sent: {enabled}")
                if result is not None:
                    if UNIT_MOTION in Devices:
                        Devices[UNIT_MOTION].Update(nValue=1 if enabled else 0, sValue=Command)
                else:
                    Domoticz.Error("Failed to set motion sensor state on Fully Kiosk device")
            elif Unit == UNIT_LOADURL:
                start_url = self.api_call("getDeviceInfo", {"type":"json"})
                if start_url:
                    start_url = start_url.get("startUrl", "")
                    if start_url:
                        self.api_call("loadUrl", {"url": start_url})
                        Domoticz.Log(f"Load Start URL command sent: {start_url}")
            elif Unit == UNIT_BRIGHTNESS and Command == "Set Level":
                level = int(Level)
                result = self.api_call("setScreenBrightness", {"value": str(level)})
                if result is not None:
                    if UNIT_BRIGHTNESS in Devices:
                        Devices[UNIT_BRIGHTNESS].Update(nValue=2 if level > 0 else 0, sValue=str(level))
                else:
                    Domoticz.Error("Failed to set brightness on Fully Kiosk device")
                self.log(f"Set brightness to: {level}")
        except Exception as e:
            Domoticz.Error(f"Error handling command: {e}")

    # ---------------------------
    # Heartbeat
    # ---------------------------
    def onHeartbeat(self):
        # Process any fetch cycle(s) the background worker finished since the
        # last tick - main/callback thread, safe here to touch Devices[...].
        while True:
            try:
                bundle = self._result_queue.get_nowait()
            except queue.Empty:
                break
            self._processHeartbeatResult(bundle)

        now = time.time()
        if now - self.last_full_refresh < self.full_refresh_interval:
            return
        self.last_full_refresh = now

        self._triggerHeartbeatFetch()

    def _triggerHeartbeatFetch(self):
        """Starts the background worker for one refresh cycle. Runs on the
        main thread; only starts a thread and returns immediately."""
        with self._fetch_lock:
            if self._fetch_in_progress:
                self.log("Fetch already in progress, skipping this heartbeat trigger.")
                return
            self._fetch_in_progress = True

        threading.Thread(target=self._fetchHeartbeatWorker, daemon=True).start()

    def _fetchHeartbeatWorker(self):
        """Runs on a background thread. Does the Fully Kiosk query and (if
        needed) the charge-control logic - handle_charge_control()/
        handle_charger_backup() only talk to Domoticz's own HTTP API, never
        to this plugin's Devices[...] dict, so they're safe to run here.
        Devices[...] updates happen in _processHeartbeatResult(), on the main
        thread, afterwards."""
        bundle = {}
        try:
            info = self.api_call("getDeviceInfo", {"type":"json"})
            bundle["info"] = info
            if not info:
                self.handle_charger_backup()
            else:
                try:
                    battery_level = int(info.get("batteryLevel", 0))
                    battery_level = max(0, min(100, battery_level))
                except Exception:
                    battery_level = None
                    bundle["battery_error"] = info.get("batteryLevel")
                bundle["battery_level"] = battery_level
                if battery_level is not None:
                    self.handle_charge_control(battery_level)
        except Exception as e:
            bundle["error"] = str(e)
        finally:
            self._result_queue.put(bundle)
            with self._fetch_lock:
                self._fetch_in_progress = False

    def _processHeartbeatResult(self, bundle):
        """Devices[...]-touching part of one refresh cycle, given the raw data
        already fetched by _fetchHeartbeatWorker(). Safe to call from the main
        thread only."""
        if "error" in bundle:
            Domoticz.Error(f"Heartbeat error: {bundle['error']}")
            return

        info = bundle.get("info")
        if not info:
            self.log("No data from Fully Kiosk received.")
            return

        if "battery_error" in bundle:
            Domoticz.Error(f"Invalid battery level received from Fully Kiosk: {bundle['battery_error']}")
        battery_level = bundle.get("battery_level")

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

    def onStop(self):
        try:
            self.http.close()
        except Exception:
            pass
        Domoticz.Log("Plugin stopped")


# ---------------------------
# Globale plugin instantie
# ---------------------------
global _plugin
_plugin = BasePlugin()

def onStart():
    _plugin.onStart()

def onStop():
    _plugin.onStop()

def onHeartbeat():
    _plugin.onHeartbeat()

def onCommand(Unit, Command, Level, Color):
    _plugin.onCommand(Unit, Command, Level, Color)
