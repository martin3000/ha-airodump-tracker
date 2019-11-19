"""Support for scanning a network with airodump."""

# TODO:
# - support exclude of MACs?
#

import math


import logging
from collections import namedtuple
from datetime import timedelta
from datetime import datetime

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util
from homeassistant.components.device_tracker import (
    DOMAIN,
    PLATFORM_SCHEMA,
    DeviceScanner,
)

_LOGGER = logging.getLogger(__name__)

#not supported yet
CONF_EXCLUDE = "exclude"

#there is the consider_home value that will make sure some time will pass before marked as away
#so we should not put a big value here...
BERLIN_INTERVAL = 15

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_EXCLUDE, default=[]): vol.All(cv.ensure_list, [cv.string]),
    }
)


def get_scanner(hass, config):
    """Validate the configuration and return a Airodump scanner."""
    return AirodumpDeviceScanner(config[DOMAIN])


AccessPoints = namedtuple("AccessPoints", ["bssid", "channel", "ssid"])
Device = namedtuple("Device", [ "mac", "name", "ssid", "channel", "power", "distance", "last_seen" ])


def convert_power_to_distance(power_dBm, wifi_channel):
    #rx power measured 1m from the transmitter clear line of sight
    P0 = -40 #todo: calculate it based on txpower also
    #we are using Log-distance Path Loss Model
    #https://pdfs.semanticscholar.org/5d18/474f224f4879a3765598713bae93f9e9c11d.pdf
    #values are calibrated from my measurements for my setup! check your setup
    alpha = 4
    if wifi_channel > 14: #5G channel
        alpha = 4
        P0 = -55
    distance = math.ceil ( math.pow(10, -((power_dBm - P0)/(10*alpha))) )
    return distance


class AirodumpDeviceScanner(DeviceScanner):
    """This class scans for devices from airodump output csv file."""

    exclude = []

    def __init__(self, config):
        """Initialize the scanner."""
        self.exclude = config[CONF_EXCLUDE]

        #devices found on our last scan
        self.devices_found = []

        _LOGGER.debug("Airodump Scanner initialized")

    def scan_devices(self):
        """Scan for new devices and return a list with found device IDs."""
        self._update_info()

        _LOGGER.debug("Airodump last results %s", self.devices_found)

        return [device.mac for device in self.devices_found]

    def get_device_name(self, device):
        """Return the name of the given device or None if we don't know."""
        filter_named = [
            result.name for result in self.devices_found if result.mac == device
        ]

        if filter_named:
            return filter_named[0]
        return None

    def get_extra_attributes(self, device):
        """Return the extra attributes(for ex distance(m)) of the given device."""
        filter_device = next(
            (result for result in self.devices_found if result.mac == device), None
        )
        return {"ssid": filter_device.ssid, "channel": filter_device.channel, "power": filter_device.power, "distance": filter_device.distance, "last_seen": filter_device.last_seen}


    def _update_info(self):
        """Scan the output csv file generated by airodump-ng for devices.

        Returns boolean if scanning successful.
        """
        _LOGGER.debug("Airodump Scanning...")

        devices_found = []
        access_points_found = []

        try:
            with open('/tmp/airodump-01.csv') as f:
                lineList = [line.rstrip() for line in f]

                now = datetime.now()

                for line in lineList:
                    cols = line.split(",")
                    mac_groups = cols[0].strip().split(":")

                    if len( mac_groups ) == 6: #check bssid & mac is valid
                        if len(cols) >= 14:
                            #accesspoint entry in the file
                            bssid = cols[0].strip().upper()
                            channel = cols[3].strip()
                            ssid = cols[13].strip()
                            access_points_found.append( AccessPoints(bssid, channel, ssid) )

                        elif len(cols) >= 7:
                            #device entry in the file
                            mac = cols[0].strip().upper()
                            bssid = cols[5].strip().upper()
                            power = cols[3].strip()
                            lastseen_str = cols[2].strip()

                            try:
                                lastseen = datetime.strptime(lastseen_str, '%Y-%m-%d %H:%M:%S')
                            except ValueError:
                                #em..probably the Last seen time string or invalid..just ignore
                                continue

                            lastseen_seconds = (now - lastseen).total_seconds()

                            distance = power #find the formula

                            #_LOGGER.debug("Airodump %s -> %s sec", mac, lastseen_seconds)

                            #Seams the -a(filter associated devices) and --berlin filters work only for the scren output, NOT for the file, so we need to filter them here
                            #1. check if associated to our access points
                            #  plus we need the channel to get better distance estimations
                            #2. return only devices that have last seen datetime less then a value
                            filtered_accespoint = [ accespoint for accespoint in access_points_found if accespoint.bssid == bssid ]
                            if filtered_accespoint and lastseen_seconds > 0 and lastseen_seconds < BERLIN_INTERVAL:
                                #Associated, take needed info also from accesspoints and save them as a Device object
                                channel = filtered_accespoint[0].channel
                                ssid = filtered_accespoint[0].ssid
                                name = 'dev_' + mac_groups[0] + "_" + mac_groups[1]
                                channel = filtered_accespoint[0].channel

                                try:
                                    distance = convert_power_to_distance( int(power) , int(channel) )
                                except ValueError:
                                    continue

                                devices_found.append( Device(mac, name, ssid, channel, power, distance, lastseen) )



                f.close()

        except OSError as e:
            print(e.strerror)

        self.devices_found = devices_found

        _LOGGER.debug("Airodump scan successful")
        return True