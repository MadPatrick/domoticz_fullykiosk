"""
<plugin key="FullyKiosk" name="Fully Kiosk plugin" author="MadPatrick" version="1.0.4" wikilink="https://www.fully-kiosk.com/" externallink="https://github.com/MadPatrick/domoticz_fullykiosk">
    <description>
        <br/>
        <h2>Fully Kiosk plugin</h2>
        <p>Version 1.0.4</p>
        <p>Supports: Screen On/Off, Screensaver, Battery, Charging, Motion, Brightness</p>
        <table border="1" cellpadding="4" cellspacing="0">
            <tr>
                <th>Parameter</th>
                <th>Description</th>
            </tr>
            <tr>
                <td>Address</td>
                <td>Fill in the IP address</td>
            </tr>
            <tr>
                <td>Port</td>
                <td>Fill in the port address</td>
            </tr>
            <tr>
                <td>Username</td>
                <td>Fill in the Username (normally blank)</td>
            </tr>
            <tr>
                <td>Password</td>
                <td>Fill in the Password software</td>
            </tr>
            <tr>
                <td>Refresh Interval (sec)</td>
                <td>Time for the next refresh</td>
            </tr>
            <tr>
                <td>Debug Log</td>
                <td>Do you want Debug logging On or Off</td>
            </tr>
        </table>
        <br/>
    </description>
    <params>
        <param field="Address" label="Tablet IP" width="200px" required="true" default="192.168.1.200"/>
        <param field="Port" label="Port" width="100px" required="true" default="2323"/>
        <param field="Username" label="Username" width="150px"/>
        <param field="Password" label="Password" width="150px" password="true"/>
        <param field="Mode1" label="Refresh Interval (sec)" width="100px" required="true" default="60"/>
        <param field="Mode6" label="Debug logging" width="100px" default="No">
            <options>
                <option label="No" value="False"/>
                <option label="Yes" value="True"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
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
        self.connected = None  # None = onbekend, True = verbonden, False = fout

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
        Domoticz.Log("Fully Kiosk plugin started")

        # Icon setup
        _IMAGE = "Fully"
        creating_new_icon = _IMAGE not in Images
        Domoticz.Image(f"{_IMAGE}.zip").Create()
        if _IMAGE in Images:
            self.imageID = Images[_IMAGE].ID
            if creating_new_icon:
                self.log("Icons created and loaded.")
            else:
                self.log(f"Icons found in database (ImageID={self.imageID}).")
        else:
            Domoticz.Log(f"Unable to load icon pack '{_IMAGE}.zip'")

        # Parameters
        self.base_url = Parameters["Address"]
        self.port = int(Parameters.get("Port", 2323))
        self.username = Parameters.get("Username", "")
        self.password = Parameters.get("Password", "")
        self.debug = Parameters.get("Mode6", "false").lower() == "true"

        # Refresh interval
        try:
            self.full_refresh_interval = max(1, int(Parameters.get("Mode1", 300)))
        except Exception:
            self.full_refresh_interval = 300
        Domoticz.Log(f"Polling interval set to {self.full_refresh_interval} seconds")

        # Korte heartbeat
        Domoticz.Heartbeat(self.heartbeat_interval)
        Domoticz.Log(f"Heartbeat interval set to {self.heartbeat_interval} seconds")

        # Devices aanmaken
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

            if created_devices:
                Domoticz.Log(f"Devices created: {', '.join(created_devices)}")
#            else:
#                Domoticz.Log("All devices already exist.")

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
            self.log(f"API call: {url} params={params}")
            r = requests.get(url, params=params, timeout=5)
            r.raise_for_status()

            # Verbinding is gelukt
            if self.connected is False:
                Domoticz.Log("Connection restored")
            self.connected = True

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

            # Log alleen als status verandert
            if self.connected is not False:
                Domoticz.Error(f"{short} (Connection failed to Tablet)")
            self.connected = False
            return None

    # ---------------------------
    # Commands
    # ---------------------------
    def onCommand(self, Unit, Command, Level, Color):
        if Unit == UNIT_SCREEN:
            cmd = "screenOn" if Command == "On" else "screenOff"
            self.api_call(cmd)
            self.log(f"Screen command sent: {cmd}")
        elif Unit == UNIT_SCREENSAVER:
            value = "true" if Command == "On" else "false"
            self.api_call("setConfig", {"key":"screensaver","value":value})
            self.log(f"Screensaver command sent: {value}")
        elif Unit == UNIT_MOTION:
            value = "true" if Command == "On" else "false"
            self.api_call("setConfig", {"key":"motionDetectionEnabled","value":value})
            self.log(f"Motion sensor command sent: {value}")
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
                return

            # Screen
            if UNIT_SCREEN in Devices:
                screen_on = info.get("screenOn", False)
                Devices[UNIT_SCREEN].Update(nValue=1 if screen_on else 0, sValue="On" if screen_on else "Off")
                self.log(f"Screen: {screen_on}")

            # Screensaver
            if UNIT_SCREENSAVER in Devices:
                screensaver_on = info.get("screensaverEnabled", False)
                Devices[UNIT_SCREENSAVER].Update(nValue=1 if screensaver_on else 0, sValue="On" if screensaver_on else "Off")
                self.log(f"Screensaver: {screensaver_on}")

            # Battery
            if UNIT_BATTERY in Devices:
                battery_level = int(info.get("batteryLevel", 0))
                battery_level = max(0, min(100, battery_level))
                Devices[UNIT_BATTERY].Update(nValue=battery_level, sValue=str(battery_level))
                self.log(f"Battery: {battery_level}%")

            # Charging
            if UNIT_CHARGING in Devices:
                charging = info.get("isPlugged", False)
                Devices[UNIT_CHARGING].Update(nValue=1 if charging else 0, sValue="On" if charging else "Off")
                self.log(f"Charging: {charging}")

            # Motion
            if UNIT_MOTION in Devices:
                motion_on = info.get("motionDetectorStarted", False)
                Devices[UNIT_MOTION].Update(nValue=1 if motion_on else 0, sValue="On" if motion_on else "Off")
                self.log(f"Motion: {motion_on}")

            # Brightness
            if UNIT_BRIGHTNESS in Devices:
                brightness = int(info.get("screenBrightness", 0))
                brightness = max(0, min(100, brightness))
                Devices[UNIT_BRIGHTNESS].Update(nValue=2 if brightness > 0 else 0, sValue=str(brightness))
                self.log(f"Brightness: {brightness}")

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
