# VO2 Max Mask v 1.1
Project to build an affordable and adaptable VO2 Max mask.  Your "VO2 Max" is a measure of how much oxygen your body and absorb and use during exercise, read more about it [here](https://www.healthline.com/health/vo2-max#benefits).

Original idea and detailed build instructions can be found on [Instructable](https://www.instructables.com/Accurate-VO2-Max-for-Zwift-and-Strava/). Other designs can be found [here, a UC Davis Med Center project](https://faculty.engineering.ucdavis.edu/knoesen/wp-content/uploads/sites/119/2016/12/OOCOO_WirelessHealthSubmission_Final.pdf) and a [commercial version that costs just under US$6,000](https://vo2master.com/).

This fork of [Meteoscientific](https://github.com/meteoscientific/VO2max) incorporates the CO2 enhancements from Ulrich Rissel.  The intent with this repo is to make a prototype that can be used by Sports technology at KTH and be adble to make future improvements.

## Design Constraints
Less than $200 for all parts and printing.
Printable by anyone with a 3D printer (easy print files).
Can handle up to 200 liters per minute of exhaled air.
Small and light enough to be worn for a full workout.
Able to measure resting as well maximum VO2.

## App Intent
Allows an athlete to monitor real time [RER](https://www.adinstruments.com/signal/rer#:~:text=Respiratory%20Exchange%20Ratio%20(RER)%20is,is%20operating%20aerobically%20or%20anaerobically.).

Allows an athlete to monitor gross mechanical efficiency

Allows for BLE or WiFi or ANT+ connections from any other sensor
- heart rate monitor
- stride rate or length
- power meter
- rowing stroke rate
  
## Versions
- V1 - Original version that works, can be found via the Instructable link above.
- V1.1 - Original version + CO2 and updated sensors due to availability
- V2 - Upgraded version by Urissel & Ivor, includes the CO2 & ambient temp/pressure to adapt to different elevation & temperatures.
- V3 - Proposed by Mahmoud, this is the T version.  Currently abandoned due to issues with getting correct sensor readings.
- V4 - Proposed by Stefan, affectionately called "The Snork".  Latest version.

## Current Status
Current Status (December 9th 2025):
The mask is now fully assembled based on the bill of materials under the EU section in the BOM file.
The Arduino v1.1 code has been updated with the latest sensor values.
Recent work includes integrating a LiPo battery and an on/off switch, making the unit fully portable without needing USB power.
Future recommendations can be found in the
[Final report](https://github.com/TobiasBrasileiroReyes/VO2max-Mask/blob/battery-and-on/off-switch-upgrade/papers/VO2MaxMaskFINAL.pdf).

## Hardware Upgrade (December 2025): Battery & On/Off Switch
1. LiPo Battery – E503450, 1000 mAh, 3.7V
	•	Installed inside the enclosure.
	•	The battery is rechargeable.

2. On/Off Switch
	•	Connected in series with the battery positive lead.
	•	Allows safe and clean power control.
	•	No need to unplug USB or open the case to turn the device off.

## Power-On Process
!! After turning on the device using the on/off switch, you must press the side button next to the display to reset the screen. Once the reset button is pressed, the system will start running. !!
Instructions on the picture below:
<figure>
    <img src="/images/arduino v1.1/battery-switch-images (upgrade)/startup_instructions.png" width="640" height="480"
         alt="Build parts">
    <figcaption>Hardware components, from the 3D printed case. </figcaption>
</figure>

## Battery Charging Instructions
The LiPo battery (E503450, 1000 mAh) is charged directly through the USB-C port on the TTGO T-Display board.
  1. Turn the on/off switch to ON (charging will NOT start if the switch is OFF)
  2. Connect a USB-C cable to the device to provide power.


### Steps to Build & Use

## Order Sensors, board, and assorted fasteners.
Check the [BOM](https://github.com/Elin310/VO2max/blob/main/BOM.md) for all the various parts to order, they can take a week or two to come in.

## Print 3D Parts
Print out the 3D parts using PLA.

The 3D parts used is under the map 3D print files under arduino v1.1 map. The buttons are not a real match to the case and the on/off switch for the battery was the wrong one and need to be adjusted.

<br>Test fit all parts and make sure you know where everything goes; that will make the next steps much easier.

## Program Board
Originally this project was built with the Arduino IDE and we decided to continue on that.

## Wire It Up
See images for guidance under the folder images/arduino v1.1

## Assembly
Make sure to fit the sensor with the tubings, or adjust the 3D print file to reflect the actual diameter on the tube.

## Arduino
Source code for Arduino under "VO2Max" - Arduino board settings to use for TTGO T-Display:

    Board: ESP32 Dev Module
    Upload Speed: 921600
    CPU Frequency: 240Mhz (WiFi/BT)
    Flash Frequency: 80Mhz
    Flash Mode: QIO
    Flash Size: 4MB (32Mb)
    Partition Scheme: Default 4MB with spiffs (1.2MB APP/1.5 SPIFFS)
    Core Debug Level: None`

## Useful Images

<figure>
    <img src="/images/arduino v1.1/Components.jpg" width="640" height="480"
         alt="Build parts">
    <figcaption>Hardware components, from the 3D printed case. </figcaption>
</figure><br><br>
<figure>
  <figure>
    <img src="/images/arduino v1.1/CO2click.jpg" width="640" height="480"
         alt="Build parts">
    <figcaption>CO2 sensor </figcaption>
</figure><br><br>
  <figure>
    <img src="/images/arduino v1.1/displayO2.jpg" width="640" height="480"
         alt="Build parts">
    <figcaption>O2 sensor connected to the ESP32 TTGO board </figcaption>
</figure><br><br>
<figure>
    <img src="/images/arduino v1.1/wiring.jpg" width="480" height="640"
         alt="Upgrading">
    <figcaption>Starting to build to use CO2 sensor. CO2 Click sensor pictured top left.</figcaption>
</figure><br><br>
<figure>
    <img src="/images/casefilling.jpg" width="640" height="480"
         alt="Upgraded build">
    <figcaption>Assembled into case tightly, BM280 barometer addition mounted onto front of tube, wiring for CO2 monitor fed behind and out to top. Picture saved from original project</figcaption>
</figure><br><br>


3D printing files are within the `design` folder, Ulrich Rissel's design files to use a larger venturi diameter with CO2 sensor holder in `design/CO2_upgrade`

## Usage - App
* Turn on device
* Add your weight (kg or lbs?)
* Push the Go button
* Turn on the Sensirion App, which will automatically pair and start recording data

Programing is done through the USB-C connector. 
(Charging the battery is accomplished by turning on the unit and then plugging it in.) - Not included in this version

The App is designed for collecting data from a CO2 sensor so you have to spoof it by sending the Volume Minute of O2 to the CO2 level screen, the VO2 max to the Temp screen and the O2 level to the Humidity screen. 


## Additional changes in this version:
- Menu system enhanced with adjustable calibration and setup options.
- Additional GoldenCheetah integration (with VO2 master output)
- CO2 sensor support (Ulrich's mods)
- Updated code, both .ino file and libraries to match the new sensors

## Running the unit on the [Sensirion MyAmbience app](https://apps.apple.com/us/app/sensirion-myambience/id1529131572) (iOS)
* VO2Max.ino
* DFRobot_OxygenSensor.cpp
* DFRobot_OxygenSensor.h
* Sensirion_GadgetBle_Lib.cpp
* Sensirion_GadgetBle_Lib.h
