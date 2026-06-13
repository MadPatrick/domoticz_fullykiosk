# 🏠 Fully Kiosk Browser Plugin for Domoticz
![Status](https://img.shields.io/badge/Status-Stable-brightgreen)
![Domoticz](https://img.shields.io/badge/Domoticz-2022%2B-blue)
![Python](https://img.shields.io/badge/Python-3.7+-yellow)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

Domoticz plugin written in Python to the Fully Kiosk Browser.  
This is an app which works very good with Dashticz on a Tablet. See https://www.fully-kiosk.com/

The plugin currently supports the following 

- Screen on/Off
- Screensaver On/Off
- Battery level
- Charging status
- Charge control via an external Domoticz switch
- Motion Sensor On/Off
- Brightness level

## 💻 Installation

### 📦 Install the plugin
1. Go in your Domoticz directory using a command line and open the plugins directory:
 ```cd domoticz/plugins```
2. clone the plugin:
 ```git clone https://github.com/MadPatrick/domoticz_fullykiosk```
2. Restart Domoticz:
 ```sudo systemctl restart domoticz```

### ⚙️ Configure the Plugin
In the Domoticz UI, navigate to the Hardware page. 
In the hardware dropdown list there will be an entry called "Fully Kiosk".
Add the hardware to your Domoticz system and fill in the required fields

Set **Charger switch ID** to the Domoticz device ID of the Z-Wave switch that switches the tablet charger. Leave it empty to disable charge control.
Set **Domoticz API Host** and **Domoticz API Port** to the local Domoticz web/API endpoint used to switch that device.
The plugin starts charging around 20-30%, stops around 80-90%, always starts at 15% or lower, always stops at 95% or higher, and switches the charger on as backup when the tablet is unreachable and the charger has been off for 12 hours.

## 🔄 Update the plugin:
When there an update of the plugin you can easlily do an update by:
```
cd domoticz/plugins/domoticz_fullykiosk
git pull
```
And then either restart Domoticz or update the plugin on the Hardware page.
