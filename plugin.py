"""
<plugin key="FullyKiosk" name="Fully Kiosk plugin" author="MadPatrick" version="1.1">
    <description>
        <h2>Fully Kiosk plugin</h2>
        <p>Version 1.1</p>
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
        self.refresh_interval = 60
        self.heartbeat_counter = 0
        self.debug = False
        self.busy = False
        self.imageID = None

    def log(self, message):
        if self.debug:
            Domoticz.Log(f"DEBUG: {message}")

    def onStart(self):
        self.debug = Parameters.get("Mode6", "false").lower() == "true"
        
        # Icon laden
        _IMAGE = "Fully"
        try:
            if _IMAGE not in Images:
                Domoticz.Image(f"{_IMAGE}.zip").Create()
            if _IMAGE in Images:
                self.imageID = Images[_IMAGE].ID
        except Exception as e:
            Domoticz.Log(f"Fout bij laden icon pack: {e}")

        self.base_url = Parameters["Address"]
        self.port = int(Parameters.get("Port", 2323))
        self.username = Parameters.get("Username", "")
        self.password = Parameters.get("Password", "")
        
        try:
            # We zetten een minimum van 10s om overbelasting te voorkomen
            self.refresh_interval = max(10, int(Parameters["Mode1"]))
        except:
            self.refresh_interval = 60

        # Belangrijk: Domoticz heartbeat kort houden, we regelen de interval zelf
        Domoticz.Heartbeat(10)
        
        self.create_devices()
        Domoticz.Log(f"Fully Kiosk Plugin gestart. Refresh: {self.refresh_interval}s")

    def create_devices(self):
        # Alle originele devices teruggezet
        if 1 not in Devices:
            Domoticz.Device(Name="Screen", Unit=1, TypeName="Switch", Used=1, Image=self.imageID).Create()
        if 2 not in Devices:
            Domoticz.Device(Name="Screensaver", Unit=2, TypeName="Switch", Used=1, Image=self.imageID).Create()
        if 3 not in Devices:
            Domoticz.Device(Name="Battery", Unit=3, Type=243, Subtype=6, Used=1).Create()
        if 4 not in Devices:
            Domoticz.Device(Name="Charging", Unit=4, TypeName="Switch", Used=1).Create()
        if 5 not in Devices:
            Domoticz.Device(Name="Motion Sensor", Unit=5, TypeName="Switch", Used=1).Create()
        if 6 not in Devices:
            Domoticz.Device(Name="Load Start URL", Unit=6, Type=244, Switchtype=9, Subtype=73, Used=1).Create()
        if 7 not in Devices:
            Domoticz.Device(Name="Brightness", Unit=7, TypeName="Dimmer", Used=1).Create()

    def api_call(self, cmd, extra_params=None):
        params = {"cmd": cmd, "password": self.password, "type": "json"}
        if self.username: params["username"] = self.username
        if extra_params: params.update(extra_params)
        
        url = f"http://{self.base_url}:{self.port}"
        try:
            # Timeout op 5 seconden om thread-hang te voorkomen
            r = requests.get(url, params=params, timeout=5)
            r.raise_for_status()
            if r.text:
                return r.json()
        except Exception as e:
            self.log(f"API Fout ({cmd}): {e}")
        return None

    def onHeartbeat(self):
        self.heartbeat_counter += 10
        if self.heartbeat_counter < self.refresh_interval:
            return

        if self.busy:
            return

        self.busy = True
        self.heartbeat_counter = 0

        try:
            info = self.api_call("getDeviceInfo")
            if info:
                # 1. Screen
                if 1 in Devices:
                    val = 1 if info.get("screenOn", False) else 0
                    Devices[1].Update(nValue=val, sValue="On" if val == 1 else "Off")
                
                # 2. Screensaver
                if 2 in Devices:
                    val = 1 if info.get("screensaverEnabled", False) else 0
                    Devices[2].Update(nValue=val, sValue="On" if val == 1 else "Off")

                # 3. Battery
                if 3 in Devices:
                    batt = info.get("batteryLevel", 0)
                    Devices[3].Update(nValue=int(batt), sValue=str(batt))
                
                # 4. Charging
                if 4 in Devices:
                    char = 1 if info.get("isPlugged", False) else 0
                    Devices[4].Update(nValue=char, sValue="On" if char == 1 else "Off")

                # 5. Motion Sensor (Status of detection is running)
                if 5 in Devices:
                    mot = 1 if info.get("motionDetectorStarted", False) else 0
                    Devices[5].Update(nValue=mot, sValue="On" if mot == 1 else "Off")

                # 7. Brightness
                if 7 in Devices:
                    bright = int(info.get("screenBrightness", 0))
                    Devices[7].Update(nValue=2 if bright > 0 else 0, sValue=str(bright))
        except Exception as e:
            Domoticz.Error(f"Fout tijdens update: {e}")
        finally:
            self.busy = False

    def onCommand(self, Unit, Command, Level, Color):
        self.log(f"Commando ontvangen - Unit: {Unit}, Command: {Command}, Level: {Level}")
        
        if Unit == 1: # Screen
            self.api_call("screenOn" if Command == "On" else "screenOff")
        
        elif Unit == 2: # Screensaver
            val = "true" if Command == "On" else "false"
            self.api_call("setConfig", {"key": "screensaver", "value": val})
            
        elif Unit == 5: # Motion detection toggle
            val = "true" if Command == "On" else "false"
            self.api_call("setConfig", {"key": "motionDetectionEnabled", "value": val})

        elif Unit == 6: # Load Start URL
            info = self.api_call("getDeviceInfo")
            url = info.get("startUrl", "") if info else ""
            if url:
                self.api_call("loadUrl", {"url": url})
                Domoticz.Log(f"Start URL geladen: {url}")

        elif Unit == 7: # Brightness
            self.api_call("setScreenBrightness", {"value": str(Level)})
            Devices[Unit].Update(nValue=int(Level), sValue=str(Level))

global _plugin
_plugin = BasePlugin()

def onStart(): _plugin.onStart()
def onHeartbeat(): _plugin.onHeartbeat()
def onCommand(Unit, Command, Level, Color): _plugin.onCommand(Unit, Command, Level, Color)
def onStop(): Domoticz.Log("Fully Kiosk plugin gestopt")
