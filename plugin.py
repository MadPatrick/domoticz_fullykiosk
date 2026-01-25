"""
<plugin key="FullyKiosk" name="Fully Kiosk plugin" author="MadPatrick" version="1.0">
    <description>
        <h2>Fully Kiosk plugin</h2>
        <p>Version 1.0</p>
        <p>Supports: Screen On/Off, Screensaver, Battery, Charging, Motion, Brightness</p>
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

class BasePlugin:
    def __init__(self):
        self.base_url = ""
        self.port = 2323
        self.username = ""
        self.password = ""
        self.devices_created = False
        self.refresh_interval = 60
        self.debug = False

    def log(self, message):
        """Log alleen als debug aanstaat"""
        if self.debug:
            Domoticz.Log(f"DEBUG: {message}")

    def onStart(self):
        Domoticz.Log("Fully Kiosk plugin started")

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
            self.error(f"Unable to load icon pack '{_IMAGE}.zip'")

        self.base_url = Parameters["Address"]
        self.port = int(Parameters.get("Port", 2323))
        self.username = Parameters.get("Username", "")
        self.password = Parameters.get("Password", "")
        self.debug = Parameters.get("Mode6", "false").lower() == "true"

        # Polling interval instellen
        try:
            self.refresh_interval = max(1, int(Parameters["Mode1"]))
        except Exception:
            self.refresh_interval = 60
        Domoticz.Heartbeat(self.refresh_interval)
        Domoticz.Log(f"Polling interval ingesteld op {self.refresh_interval} seconden")

        # Devices aanmaken
        if not self.devices_created:
            if 1 not in Devices:
                Domoticz.Device(Name="Screen", Unit=1, TypeName="Switch",Used=1,Image=self.imageID).Create()
            if 2 not in Devices:
                Domoticz.Device(Name="Screensaver", Unit=2, TypeName="Switch",Used=1,Image=self.imageID).Create()
            if 3 not in Devices:
                Domoticz.Device(Name="Battery", Unit=3, Type=243, Subtype=6,Used=1,Image=self.imageID).Create()
            if 4 not in Devices:
                Domoticz.Device(Name="Charging", Unit=4, TypeName="Switch",Used=1,Image=self.imageID).Create()
            if 5 not in Devices:
                Domoticz.Device(Name="Motion Sensor", Unit=5, TypeName="Switch",Used=1,Image=self.imageID).Create()
            if 6 not in Devices:
                Domoticz.Device(Name="Load Start URL", Unit=6, Type=244,Switchtype = 9, Subtype = 73, Used=1,Image=self.imageID).Create()
            if 7 not in Devices:
                Domoticz.Device(Name="Brightness", Unit=7, TypeName="Dimmer",Used=1,Image=self.imageID).Create()
            self.devices_created = True

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
            try:
                data = r.json()
                self.log(f"API response: {data}")
                return data
            except Exception:
                self.log(f"API returned non-JSON: {r.text}")
                return None
        except Exception as e:
            Domoticz.Log(f"API call error: {e}")
            return None

    def onCommand(self, Unit, Command, Level, Color):
        if Unit == 1:  # Screen On/Off
            cmd = "screenOn" if Command == "On" else "screenOff"
            self.api_call(cmd)
            self.log(f"Screen command sent: {cmd}")
        elif Unit == 2:  # Screensaver On/Off
            value = "true" if Command == "On" else "false"
            self.api_call("setConfig", {"key":"screensaver","value":value})
            self.log(f"Screensaver command sent: {value}")
        elif Unit == 5:  # Motion Sensor
            value = "true" if Command == "On" else "false"
            self.api_call("setConfig", {"key":"motionDetectionEnabled","value":value})
            self.log(f"Motion sensor command sent: {value}")
        elif Unit == 6:  # Load Start URL
            start_url = self.api_call("getDeviceInfo", {"type":"json"}).get("startUrl", "")
            if start_url:
                self.api_call("loadUrl", {"url": start_url})
                Domoticz.Log(f"Load Start URL command sent: {start_url}")
        elif Unit == 7 and Command == "Set Level":  # Brightness dimmer
            level = int(Level)
            self.api_call("setScreenBrightness", {"value": str(level)})
            Devices[7].Update(nValue=level, sValue=str(level))
            self.log(f"Set brightness to: {level}")

    def onHeartbeat(self):
        info = self.api_call("getDeviceInfo", {"type":"json"})
        if not info:
            return

        if 1 in Devices:
            screen_on = info.get("screenOn", False)
            Devices[1].Update(nValue=1 if screen_on else 0, sValue="On" if screen_on else "Off")
            self.log(f"Screen: {screen_on}")

        if 2 in Devices:
            screensaver_on = info.get("screensaverEnabled", False)
            Devices[2].Update(nValue=1 if screensaver_on else 0, sValue="On" if screensaver_on else "Off")
            self.log(f"Screensaver: {screensaver_on}")

        if 3 in Devices:
            battery_level = int(info.get("batteryLevel", 0))
            Devices[3].Update(nValue=battery_level, sValue=str(battery_level))
            self.log(f"Battery: {battery_level}%")

        if 4 in Devices:
            charging = info.get("isPlugged", False)
            Devices[4].Update(nValue=1 if charging else 0, sValue="On" if charging else "Off")
            self.log(f"Charging: {charging}")

        if 5 in Devices:
            motion_on = info.get("motionDetectorStarted", False)
            Devices[5].Update(nValue=1 if motion_on else 0, sValue="On" if motion_on else "Off")
            self.log(f"Motion: {motion_on}")

        if 7 in Devices:
            brightness = int(info.get("screenBrightness", 0))
            Devices[7].Update(
                nValue=1,
                sValue=str(brightness)
            )

# Globale plugin instantie
global _plugin
_plugin = BasePlugin()

def onStart():
    _plugin.onStart()

def onStop():
    Domoticz.Log("Fully Kiosk plugin stopped")

def onHeartbeat():
    _plugin.onHeartbeat()

def onCommand(Unit, Command, Level, Color):
    _plugin.onCommand(Unit, Command, Level, Color)
