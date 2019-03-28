#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#    NRSC5 GUI - A graphical interface for nrsc5
#    Copyright (C) 2017-2019  Cody Nybo & Clayton Smith
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

import datetime
import glob
import io
import json
import logging
import os
import queue
import re
import sys
import tempfile
import threading
import time
from dateutil import tz
from PIL import Image, ImageFont, ImageDraw
import pyaudio

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GObject, Gdk, GdkPixbuf

import nrsc5

class NRSC5_GUI(object):
    AUDIO_SAMPLE_RATE = 44100
    AUDIO_SAMPLES_PER_FRAME = 2048
    MAP_FILE = "map.png"

    logLevel = 20                       # decrease to 10 to enable debug logs

    def __init__(self):
        logging.basicConfig(level=self.logLevel,
                            format="%(asctime)s %(levelname)-5s %(filename)s:%(lineno)d: %(message)s",
                            datefmt="%H:%M:%S")

        GObject.threads_init()

        self.getControls()              # get controls and windows
        self.initStreamInfo()           # initilize stream info and clear status widgets

        self.radio = None
        self.audio_queue = queue.Queue(maxsize=64)
        self.audio_thread = threading.Thread(target=self.audio_worker)
        self.playing = False            # currently playing
        self.statusTimer = None         # status update timer
        self.imageChanged = False       # has the album art changed
        self.xhdrChanged = False        # has the HDDR data changed
        self.lastImage = ""             # last image file displayed
        self.lastXHDR = ""              # the last XHDR data received
        self.stationStr = ""            # current station frequency (string)
        self.streamNum = 0              # current station stream number
        self.bookmarks = []             # station bookmarks
        self.stationLogos = {}          # station logos
        self.bookmarked = False         # is current station bookmarked
        self.mapViewer = None           # map viewer window
        self.weatherMaps = []           # list of current weathermaps sorted by time
        self.trafficMap = Image.new("RGB", (600, 600), "white")
        self.mapTiles = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        self.mapData = {
            "mapMode": 1,
            "weatherTime": 0,
            "weatherPos": [0, 0, 0, 0],
            "weatherNow": "",
            "weatherID": "",
            "viewerConfig": {
                "mode": 1,
                "animate": False,
                "scale": True,
                "windowPos": (0, 0),
                "windowSize": (764, 632),
                "animationSpeed": 0.5
            }
        }

        # setup bookmarks listview
        nameRenderer = Gtk.CellRendererText()
        nameRenderer.set_property("editable", True)
        nameRenderer.connect("edited", self.on_bookmark_name_edited)

        colStation = Gtk.TreeViewColumn("Station", Gtk.CellRendererText(), text=0)
        colName = Gtk.TreeViewColumn("Name", nameRenderer, text=1)

        colStation.set_resizable(True)
        colStation.set_sort_column_id(2)
        colName.set_resizable(True)
        colName.set_sort_column_id(1)

        self.lv_bookmarks.append_column(colStation)
        self.lv_bookmarks.append_column(colName)

        self.loadSettings()
        self.processWeatherMaps()

        self.audio_thread.start()

    def display_logo(self):
        if self.stationStr in self.stationLogos:
            # show station logo if it's cached
            logo = os.path.join(self.aasDir, self.stationLogos[self.stationStr][self.streamNum])
            if os.path.isfile(logo):
                self.streamInfo["Logo"] = self.stationLogos[self.stationStr][self.streamNum]
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(logo)
                pixbuf = pixbuf.scale_simple(200, 200, GdkPixbuf.InterpType.HYPER)
                self.imgCover.set_from_pixbuf(pixbuf)
        else:
            # add entry in database for the station if it doesn't exist
            self.stationLogos[self.stationStr] = ["", "", "", ""]

    def on_btn_play_clicked(self, _btn):
        # start playback
        if not self.playing:

            # update all of the spin buttons to prevent the text from sticking
            self.spinFreq.update()
            self.spin_stream.update()
            self.spinGain.update()
            self.spinPPM.update()
            self.spinRTL.update()

            # start the timer
            self.statusTimer = threading.Timer(1, self.checkStatus)
            self.statusTimer.start()

            # disable the controls
            self.spinFreq.set_sensitive(False)
            self.spinGain.set_sensitive(False)
            self.spinPPM.set_sensitive(False)
            self.spinRTL.set_sensitive(False)
            self.btn_play.set_sensitive(False)
            self.btn_stop.set_sensitive(True)
            self.cb_auto_gain.set_sensitive(False)
            self.playing = True
            self.lastXHDR = ""

            self.play()

            self.stationStr = str(self.spinFreq.get_value())
            self.streamNum = int(self.spin_stream.get_value())-1

            self.display_logo()

            # check if station is bookmarked
            self.bookmarked = False
            freq = int((self.spinFreq.get_value()+0.005)*100) + int(self.spin_stream.get_value())
            for b in self.bookmarks:
                if b[2] == freq:
                    self.bookmarked = True
                    break

            self.btn_bookmark.set_sensitive(not self.bookmarked)
            if self.notebook_main.get_current_page() != 3:
                self.btn_delete.set_sensitive(self.bookmarked)

    def on_btn_stop_clicked(self, _btn):
        # stop playback
        if self.playing:
            self.playing = False

            # shutdown nrsc5
            if self.radio:
                self.radio.stop()
                self.radio.close()
                self.radio = None

            # stop timer
            self.statusTimer.cancel()
            self.statusTimer = None

            # enable controls
            if not self.cb_auto_gain.get_active():
                self.spinGain.set_sensitive(True)
            self.spinFreq.set_sensitive(True)
            self.spinPPM.set_sensitive(True)
            self.spinRTL.set_sensitive(True)
            self.btn_play.set_sensitive(True)
            self.btn_stop.set_sensitive(False)
            self.btn_bookmark.set_sensitive(False)
            self.cb_auto_gain.set_sensitive(True)

            # clear stream info
            self.initStreamInfo()

            self.btn_bookmark.set_sensitive(False)
            if self.notebook_main.get_current_page() != 3:
                self.btn_delete.set_sensitive(False)

    def on_btn_bookmark_clicked(self, _btn):
        # pack frequency and channel number into one int
        freq = int((self.spinFreq.get_value()+0.005)*100) + int(self.spin_stream.get_value())

        # create bookmark
        bookmark = [
            "{:4.1f}-{:1.0f}".format(self.spinFreq.get_value(), self.spin_stream.get_value()),
            self.streamInfo["Callsign"],
            freq
        ]
        self.bookmarked = True                  # mark as bookmarked
        self.bookmarks.append(bookmark)         # store bookmark in array
        self.lsBookmarks.append(bookmark)       # add bookmark to listview
        self.btn_bookmark.set_sensitive(False)   # disable bookmark button

        if self.notebook_main.get_current_page() != 3:
            self.btn_delete.set_sensitive(True)  # enable delete button

    def on_btn_delete_clicked(self, _btn):
        # select current station if not on bookmarks page
        if self.notebook_main.get_current_page() != 3:
            station = int((self.spinFreq.get_value()+0.005)*100) + int(self.spin_stream.get_value())
            for i in range(len(self.lsBookmarks)):
                if self.lsBookmarks[i][2] == station:
                    self.lv_bookmarks.set_cursor(i)
                    break

        # get station of selected row
        model, tree_iter = self.lv_bookmarks.get_selection().get_selected()
        station = model.get_value(tree_iter, 2)

        # remove row
        model.remove(tree_iter)

        # remove bookmark
        for i in range(len(self.bookmarks)):
            if self.bookmarks[i][2] == station:
                self.bookmarks.pop(i)
                break

        if self.notebook_main.get_current_page() != 3 and self.playing:
            self.btn_bookmark.set_sensitive(True)
            self.bookmarked = False

    def on_btn_about_activate(self, _btn):
        # sets up and displays about dialog
        if self.about_dialog:
            self.about_dialog.present()
            return

        authors = [
            "Cody Nybo <cmnybo@gmail.com>",
            "Clayton Smith <argilo@gmail.com>",
        ]

        nrsc5_gui_license = """
        NRSC5 GUI - A graphical interface for nrsc5
        Copyright (C) 2017-2019  Cody Nybo & Clayton Smith

        This program is free software: you can redistribute it and/or modify
        it under the terms of the GNU General Public License as published by
        the Free Software Foundation, either version 3 of the License, or
        (at your option) any later version.

        This program is distributed in the hope that it will be useful,
        but WITHOUT ANY WARRANTY; without even the implied warranty of
        MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
        GNU General Public License for more details.

        You should have received a copy of the GNU General Public License
        along with this program.  If not, see <https://www.gnu.org/licenses/>."""

        about_dialog = Gtk.AboutDialog()
        about_dialog.set_transient_for(self.mainWindow)
        about_dialog.set_destroy_with_parent(True)
        about_dialog.set_name("NRSC5 GUI")
        about_dialog.set_version("1.1.2")
        about_dialog.set_copyright("Copyright \u00A9 2017-2019 Cody Nybo & Clayton Smith")
        about_dialog.set_website("https://github.com/cmnybo/nrsc5-gui")
        about_dialog.set_comments("A graphical interface for nrsc5.")
        about_dialog.set_authors(authors)
        about_dialog.set_license(nrsc5_gui_license)
        about_dialog.set_logo(GdkPixbuf.Pixbuf.new_from_file("logo.png"))

        # callbacks for destroying the dialog
        def close(dialog, _response, editor):
            editor.about_dialog = None
            dialog.destroy()

        def delete_event(_dialog, _event, editor):
            editor.about_dialog = None
            return True

        about_dialog.connect("response", close, self)
        about_dialog.connect("delete-event", delete_event, self)

        self.about_dialog = about_dialog
        about_dialog.show()

    def on_spin_stream_value_changed(self, _spin):
        self.lastXHDR = ""
        self.streamInfo["Title"] = ""
        self.streamInfo["Album"] = ""
        self.streamInfo["Artist"] = ""
        self.streamInfo["Cover"] = ""
        self.streamInfo["Logo"] = ""
        self.streamInfo["Bitrate"] = 0
        self.streamNum = int(self.spin_stream.get_value())-1
        if self.playing:
            self.display_logo()

    def on_cb_auto_gain_toggled(self, btn):
        self.spinGain.set_sensitive(not btn.get_active())
        self.lblGain.set_visible(btn.get_active())

    def on_lv_bookmarks_row_activated(self, treeview, path, _view_column):
        if path:
            # get station from bookmark row
            tree_iter = treeview.get_model().get_iter(path[0])
            station = treeview.get_model().get_value(tree_iter, 2)

            # set frequency and stream
            self.spinFreq.set_value(float(int(station/10)/10.0))
            self.spin_stream.set_value(station % 10)

            # stop playback if playing
            if self.playing:
                self.on_btn_stop_clicked(None)

            # play bookmarked station
            self.on_btn_play_clicked(None)

    def on_lv_bookmarks_selection_changed(self, _tree_selection):
        # enable delete button if bookmark is selected
        _, pathlist = self.lv_bookmarks.get_selection().get_selected_rows()
        self.btn_delete.set_sensitive(len(pathlist) != 0)

    def on_bookmark_name_edited(self, _cell, path, text, _data=None):
        # update name in listview
        tree_iter = self.lsBookmarks.get_iter(path)
        self.lsBookmarks.set(tree_iter, 1, text)

        # update name in bookmarks array
        for b in self.bookmarks:
            if b[2] == self.lsBookmarks[path][2]:
                b[1] = text
                break

    def on_notebook_main_switch_page(self, _notebook, _page, page_num):
        # disable delete button if not on bookmarks page and station is not bookmarked
        if page_num != 3 and (not self.bookmarked or not self.playing):
            self.btn_delete.set_sensitive(False)
        # enable delete button if not on bookmarks page and station is bookmarked
        elif page_num != 3 and self.bookmarked:
            self.btn_delete.set_sensitive(True)
        # enable delete button if on bookmarks page and a bookmark is selected
        else:
            _, tree_iter = self.lv_bookmarks.get_selection().get_selected()
            self.btn_delete.set_sensitive(tree_iter is not None)

    def on_rad_map_toggled(self, btn):
        if btn.get_active():
            if btn == self.rad_map_traffic:
                self.mapData["mapMode"] = 0
                mapFile = os.path.join("map", "TrafficMap.png")
                if os.path.isfile(mapFile):                                                             # check if map exists
                    mapImg = Image.open(mapFile).resize((200, 200), Image.LANCZOS)                      # scale map to fit window
                    self.imgMap.set_from_pixbuf(imgToPixbuf(mapImg))                                    # convert image to pixbuf and display
                else:
                    self.imgMap.set_from_stock(Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)     # display missing image if file is not found

            elif btn == self.rad_map_weather:
                self.mapData["mapMode"] = 1
                if os.path.isfile(self.mapData["weatherNow"]):
                    mapImg = Image.open(self.mapData["weatherNow"]).resize((200, 200), Image.LANCZOS)   # scale map to fit window
                    self.imgMap.set_from_pixbuf(imgToPixbuf(mapImg))                                    # convert image to pixbuf and display
                else:
                    self.imgMap.set_from_stock(Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)     # display missing image if file is not found

    def on_btn_map_clicked(self, _btn):
        # open map viewer window
        if self.mapViewer is None:
            self.mapViewer = NRSC5_Map(self, self.mapViewerCallback, self.mapData)
            self.mapViewer.map_window.show()

    def mapViewerCallback(self):
        # delete the map viewer
        self.mapViewer = None

    def play(self):
        self.radio = nrsc5.NRSC5(lambda type, evt: self.callback(type, evt))
        self.radio.open(int(self.spinRTL.get_value()), int(self.spinPPM.get_value()))
        self.radio.set_auto_gain(self.cb_auto_gain.get_active())

        # set gain if auto gain is not selected
        if not self.cb_auto_gain.get_active():
            self.streamInfo["Gain"] = self.spinGain.get_value()
            self.radio.set_gain(self.streamInfo["Gain"])

        self.radio.set_frequency(self.spinFreq.get_value() * 1e6)
        self.radio.start()

    def checkStatus(self):
        # update status information
        def update():
            Gdk.threads_enter()
            try:
                imagePath = ""
                image = ""
                ber = [self.streamInfo["BER"][0]*100, self.streamInfo["BER"][1]*100, self.streamInfo["BER"][2]*100, self.streamInfo["BER"][3]*100]
                self.txtTitle.set_text(self.streamInfo["Title"])
                self.txtArtist.set_text(self.streamInfo["Artist"])
                self.txtAlbum.set_text(self.streamInfo["Album"])
                self.lblBitRate.set_label("{:3.1f} kbps".format(self.streamInfo["Bitrate"]))
                self.lblBitRate2.set_label("{:3.1f} kbps".format(self.streamInfo["Bitrate"]))
                self.lblError.set_label("{:2.2f}% Error ".format(ber[1]))
                self.lblCall.set_label(" " + self.streamInfo["Callsign"])
                self.lblName.set_label(self.streamInfo["Callsign"])
                self.lblSlogan.set_label(self.streamInfo["Slogan"])
                self.lblSlogan.set_tooltip_text(self.streamInfo["Slogan"])
                self.lblMerLower.set_label("{:1.2f} dB".format(self.streamInfo["MER"][0]))
                self.lblMerUpper.set_label("{:1.2f} dB".format(self.streamInfo["MER"][1]))
                self.lblBerNow.set_label("{:1.3f}% (Now)".format(ber[0]))
                self.lblBerAvg.set_label("{:1.3f}% (Avg)".format(ber[1]))
                self.lblBerMin.set_label("{:1.3f}% (Min)".format(ber[2]))
                self.lblBerMax.set_label("{:1.3f}% (Max)".format(ber[3]))

                if self.cb_auto_gain.get_active():
                    self.spinGain.set_value(self.streamInfo["Gain"])
                    self.lblGain.set_label("{:2.1f}dB".format(self.streamInfo["Gain"]))

                if self.lastXHDR == 0:
                    imagePath = os.path.join(self.aasDir, self.streamInfo["Cover"])
                    image = self.streamInfo["Cover"]
                elif self.lastXHDR == 1:
                    imagePath = os.path.join(self.aasDir, self.streamInfo["Logo"])
                    image = self.streamInfo["Logo"]
                    if not os.path.isfile(imagePath):
                        self.imgCover.clear()

                # resize and display image if it changed and exists
                if self.xhdrChanged and self.lastImage != image and os.path.isfile(imagePath):
                    self.xhdrChanged = False
                    self.lastImage = image
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(imagePath)
                    pixbuf = pixbuf.scale_simple(200, 200, GdkPixbuf.InterpType.HYPER)
                    self.imgCover.set_from_pixbuf(pixbuf)
                    logging.debug("Image changed")
            finally:
                Gdk.threads_leave()

        if self.playing:
            GObject.idle_add(update)
            self.statusTimer = threading.Timer(1, self.checkStatus)
            self.statusTimer.start()

    def processTrafficMap(self, fileName, data):
        r = re.compile(r"^TMT_.*_([1-3])_([1-3])_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2}).*$")              # match file name
        m = r.match(fileName)

        if m:
            x = int(m.group(1))-1  # X position
            y = int(m.group(2))-1  # Y position

            # get time from map tile and convert to local time
            dt = datetime.datetime(int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7)), tzinfo=tz.tzutc())
            ts = dtToTs(dt)                                                                             # unix timestamp (utc)

            # check if the tile has already been loaded
            if self.mapTiles[x][y] == ts:
                return                                                                                  # no need to recreate the map if it hasn't changed

            logging.debug("Got traffic map tile: %s, %s", x, y)

            self.mapTiles[x][y] = ts                                                                    # store time for current tile
            self.trafficMap.paste(Image.open(io.BytesIO(data)), (y*200, x*200))                         # paste tile into map

            # check if all of the tiles are loaded
            if self.checkTiles(ts):
                logging.debug("Got complete traffic map")
                self.trafficMap.save(os.path.join("map", "TrafficMap.png"))                             # save traffic map

                # display on map page
                if self.rad_map_traffic.get_active():
                    imgMap = self.trafficMap.resize((200, 200), Image.LANCZOS)                          # scale map to fit window
                    self.imgMap.set_from_pixbuf(imgToPixbuf(imgMap))                                    # convert image to pixbuf and display

                if self.mapViewer is not None:
                    self.mapViewer.updated()                                                            # notify map viwerer if it's open

    def processWeatherOverlay(self, fileName, data):
        r = re.compile(r"^DWRO_(.*)_.*_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2}).*$")                        # match file name
        m = r.match(fileName)

        if m:
            # get time from map tile and convert to local time
            dt = datetime.datetime(int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)), tzinfo=tz.tzutc())
            t = dt.astimezone(tz.tzlocal())                                                             # local time
            ts = dtToTs(dt)                                                                             # unix timestamp (utc)
            map_id = self.mapData["weatherID"]

            if m.group(1) != map_id:
                logging.error("Received weather overlay with the wrong ID: %s", m.group(1))
                return

            if self.mapData["weatherTime"] == ts:
                return                                                                                  # no need to recreate the map if it hasn't changed

            logging.debug("Got weather overlay")

            self.mapData["weatherTime"] = ts                                                            # store time for current overlay
            wxMapPath = os.path.join("map", "WeatherMap_{}_{}.png".format(map_id, ts))

            # create weather map
            try:
                mapPath = os.path.join("map", "BaseMap_" + map_id + ".png")                             # get path to base map
                if not os.path.isfile(mapPath):                                                         # make sure base map exists
                    self.makeBaseMap(self.mapData["weatherID"], self.mapData["weatherPos"])             # create base map if it doesn't exist

                imgMap = Image.open(mapPath).convert("RGBA")                                            # open map image
                posTS = (imgMap.size[0]-235, imgMap.size[1]-29)                                         # calculate position to put timestamp (bottom right)
                imgTS = self.mkTimestamp(t, imgMap.size, posTS)                                         # create timestamp
                imgRadar = Image.open(io.BytesIO(data)).convert("RGBA")                                 # open radar overlay
                imgRadar = imgRadar.resize(imgMap.size, Image.LANCZOS)                                  # resize radar overlay to fit the map
                imgMap = Image.alpha_composite(imgMap, imgRadar)                                        # overlay radar image on map
                imgMap = Image.alpha_composite(imgMap, imgTS)                                           # overlay timestamp
                imgMap.save(wxMapPath)                                                                  # save weather map
                self.mapData["weatherNow"] = wxMapPath

                # display on map page
                if self.rad_map_weather.get_active():
                    imgMap = imgMap.resize((200, 200), Image.LANCZOS)                                   # scale map to fit window
                    self.imgMap.set_from_pixbuf(imgToPixbuf(imgMap))                                    # convert image to pixbuf and display

                self.processWeatherMaps()                                                               # get rid of old maps and add new ones to the list
                if self.mapViewer is not None:
                    self.mapViewer.updated()                                                            # notify map viwerer if it's open

            except OSError:
                logging.error("Error creating weather map")
                self.mapData["weatherTime"] = 0

    def processWeatherInfo(self, data):
        weatherID = None
        weatherPos = None

        for line in data.decode().split("\n"):                                                          # read line by line
            if "DWR_Area_ID=" in line:                                                                  # look for line with "DWR_Area_ID=" in it
                # get ID from line
                r = re.compile("^DWR_Area_ID=\"(.+)\"$")
                m = r.match(line)
                weatherID = m.group(1)

            elif "Coordinates=" in line:                                                                # look for line with "Coordinates=" in it
                # get coordinates from line
                r = re.compile(r"^Coordinates=.*\((-?\d+\.\d+),(-?\d+\.\d+)\).*\((-?\d+\.\d+),(-?\d+\.\d+)\).*$")
                m = r.match(line)
                weatherPos = [float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))]

        if weatherID is not None and weatherPos is not None:                                            # check if ID and position were found
            if self.mapData["weatherID"] != weatherID or self.mapData["weatherPos"] != weatherPos:      # check if ID or position has changed
                logging.debug("Got position: (%n, %n) (%n, %n)", *weatherPos)
                self.mapData["weatherID"] = weatherID                                                   # set weather ID
                self.mapData["weatherPos"] = weatherPos                                                 # set weather map position

                self.makeBaseMap(weatherID, weatherPos)
                self.weatherMaps = []
                self.processWeatherMaps()

    def processWeatherMaps(self):
        numberOfMaps = 0
        r = re.compile("^map.WeatherMap_([a-zA-Z0-9]+)_([0-9]+).png")
        now = dtToTs(datetime.datetime.now(tz.tzutc()))                                                 # get current time
        files = glob.glob(os.path.join("map", "WeatherMap_") + "*.png")                                 # look for weather map files
        files.sort()                                                                                    # sort files
        for f in files:
            m = r.match(f)                                                                              # match regex
            if m:
                map_id = m.group(1)                                                                     # location ID
                ts = int(m.group(2))                                                                    # timestamp (UTC)

                # remove weather maps older than 12 hours
                if now - ts > 60*60*12:
                    try:
                        if f in self.weatherMaps:
                            self.weatherMaps.pop(self.weatherMaps.index(f))                             # remove from list
                        os.remove(f)                                                                    # remove file
                        logging.debug("Deleted old weather map: %s", f)
                    except OSError:
                        logging.error("Failed to delete old weather map: %s", f)

                # skip if not the correct location
                elif map_id == self.mapData["weatherID"]:
                    if f not in self.weatherMaps:
                        self.weatherMaps.append(f)                                                      # add to list
                    numberOfMaps += 1

        logging.debug("Found %s weather maps", numberOfMaps)

    def getMapArea(self, lat1, lon1, lat2, lon2):
        from math import asinh, tan, radians

        # get pixel coordinates from latitude and longitude
        # calculations taken from https://github.com/KYDronePilot/hdfm
        top = asinh(tan(radians(52.482780)))
        lat1 = top - asinh(tan(radians(lat1)))
        lat2 = top - asinh(tan(radians(lat2)))
        x1 = (lon1 + 130.781250) * 7162 / 39.34135
        x2 = (lon2 + 130.781250) * 7162 / 39.34135
        y1 = lat1 * 3565 / (top - asinh(tan(radians(38.898))))
        y2 = lat2 * 3565 / (top - asinh(tan(radians(38.898))))

        return (int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))

    def makeBaseMap(self, map_id, pos):
        mapPath = os.path.join("map", "BaseMap_" + map_id + ".png")                             # get map path
        if os.path.isfile(self.MAP_FILE):
            if not os.path.isfile(mapPath):                                                     # check if the map has already been created for this location
                logging.debug("Creating new map: %s", mapPath)
                px = self.getMapArea(*pos)                                                      # convert map locations to pixel coordinates
                mapImg = Image.open(self.MAP_FILE).crop(px)                                     # open the full map and crop it to the coordinates
                mapImg.save(mapPath)                                                            # save the cropped map to disk for later use
                logging.debug("Finished creating map")
        else:
            logging.error("Map file not found: %s", self.MAP_FILE)
            mapImg = Image.new("RGBA", (pos[2]-pos[1], pos[3]-pos[1]), "white")                 # if the full map is not available, use a blank image
            mapImg.save(mapPath)

    def checkTiles(self, t):
        # check if all the tiles have been received
        for i in range(3):
            for j in range(3):
                if self.mapTiles[i][j] != t:
                    return False
        return True

    def mkTimestamp(self, t, size, pos):
        # create a timestamp image to overlay on the weathermap
        x, y = pos
        text = "{:04g}-{:02g}-{:02g} {:02g}:{:02g}".format(t.year, t.month, t.day, t.hour, t.minute)    # format timestamp
        imgTS = Image.new("RGBA", size, (0, 0, 0, 0))                                                   # create a blank image
        draw = ImageDraw.Draw(imgTS)                                                                    # the drawing object
        font = ImageFont.truetype("DejaVuSansMono.ttf", 24)                                             # DejaVu Sans Mono 24pt font
        draw.rectangle((x, y, x+231, y+25), outline="black", fill=(128, 128, 128, 96))                  # draw a box around the text
        draw.text((x+3, y), text, fill="black", font=font)                                              # draw the text
        return imgTS                                                                                    # return the image

    def audio_worker(self):
        p = pyaudio.PyAudio()
        try:
            index = p.get_default_output_device_info()["index"]
            stream = p.open(format=pyaudio.paInt16,
                            channels=2,
                            rate=self.AUDIO_SAMPLE_RATE,
                            output_device_index=index,
                            output=True)
        except OSError:
            logging.warning("No audio output device available")
            stream = None

        while True:
            samples = self.audio_queue.get()
            if samples is None:
                break
            if stream:
                stream.write(samples)
            self.audio_queue.task_done()

        if stream:
            stream.stop_stream()
            stream.close()
        p.terminate()

    def update_bitrate(self, bits):
        kbps = bits * self.AUDIO_SAMPLE_RATE / self.AUDIO_SAMPLES_PER_FRAME / 1000
        if self.streamInfo["Bitrate"] == 0:
            self.streamInfo["Bitrate"] = kbps
        else:
            self.streamInfo["Bitrate"] = 0.99 * self.streamInfo["Bitrate"] + 0.01 * kbps

    def update_ber(self, cber):
        ber = self.streamInfo["BER"]
        if ber[0] == ber[1] == ber[2] == ber[3] == 0:
            ber[0] = cber
            ber[1] = cber
            ber[2] = cber
            ber[3] = cber
        else:
            ber[0] = cber
            ber[1] = 0.9 * ber[1] + 0.1 * cber
            if cber < ber[2]:
                ber[2] = cber
            if cber > ber[3]:
                ber[3] = cber

    def callback(self, evt_type, evt):
        if evt_type == nrsc5.EventType.LOST_DEVICE:
            pass  # TODO: update the GUI?
        elif evt_type == nrsc5.EventType.SYNC:
            self.streamInfo["Gain"] = self.radio.get_gain()
            # TODO: update the GUI?
        elif evt_type == nrsc5.EventType.LOST_SYNC:
            pass  # TODO: update the GUI?
        elif evt_type == nrsc5.EventType.MER:
            self.streamInfo["MER"] = [evt.lower, evt.upper]
        elif evt_type == nrsc5.EventType.BER:
            self.update_ber(evt.cber)
        elif evt_type == nrsc5.EventType.HDC:
            if evt.program == self.streamNum:
                self.update_bitrate(len(evt.data) * 8)
        elif evt_type == nrsc5.EventType.AUDIO:
            if evt.program == self.streamNum:
                self.audio_queue.put(evt.data)
        elif evt_type == nrsc5.EventType.ID3:
            if evt.program == self.streamNum:
                if evt.title:
                    self.streamInfo["Title"] = evt.title
                if evt.artist:
                    self.streamInfo["Artist"] = evt.artist
                if evt.album:
                    self.streamInfo["Album"] = evt.album
                if evt.xhdr:
                    if evt.xhdr.param != self.lastXHDR:
                        self.lastXHDR = evt.xhdr.param
                        self.xhdrChanged = True
                        logging.debug("XHDR changed: %s", evt.xhdr.param)
        elif evt_type == nrsc5.EventType.SIG:
            for service in evt:
                if service.type == nrsc5.ServiceType.AUDIO:
                    for component in service.components:
                        if component.type == nrsc5.ComponentType.DATA:
                            if component.data.mime == nrsc5.MIMEType.PRIMARY_IMAGE:
                                self.streams[service.number-1]["image"] = component.data.port
                            elif component.data.mime == nrsc5.MIMEType.STATION_LOGO:
                                self.streams[service.number-1]["logo"] = component.data.port
                elif service.type == nrsc5.ServiceType.DATA:
                    for component in service.components:
                        if component.type == nrsc5.ComponentType.DATA:
                            if component.data.mime == nrsc5.MIMEType.TTN_STM_TRAFFIC:
                                self.trafficPort = component.data.port
                            elif component.data.mime == nrsc5.MIMEType.TTN_STM_WEATHER:
                                self.weatherPort = component.data.port
        elif evt_type == nrsc5.EventType.LOT:
            logging.debug("LOT port=%s", evt.port)

            if self.mapDir is not None:
                if evt.port == self.trafficPort:
                    if evt.name.startswith("TMT_"):
                        self.processTrafficMap(evt.name, evt.data)
                elif evt.port == self.weatherPort:
                    if evt.name.startswith("DWRO_"):
                        self.processWeatherOverlay(evt.name, evt.data)
                    elif evt.name.startswith("DWRI_"):
                        self.processWeatherInfo(evt.data)

            if self.aasDir is not None:
                path = os.path.join(self.aasDir, evt.name)
                for i, stream in enumerate(self.streams):
                    if evt.port == stream.get("image"):
                        logging.debug("Got album cover: %s", evt.name)
                        with open(path, "wb") as f:
                            f.write(evt.data)
                        if i == self.streamNum:
                            self.streamInfo["Cover"] = evt.name
                    elif evt.port == stream.get("logo"):
                        logging.debug("Got station logo: %s", evt.name)
                        with open(path, "wb") as f:
                            f.write(evt.data)
                        self.stationLogos[self.stationStr][i] = evt.name
                        if i == self.streamNum:
                            self.streamInfo["Logo"] = evt.name

        elif evt_type == nrsc5.EventType.SIS:
            if evt.name:
                self.streamInfo["Callsign"] = evt.name
            if evt.slogan:
                self.streamInfo["Slogan"] = evt.slogan

    def getControls(self):
        # setup gui
        builder = Gtk.Builder()
        builder.add_from_file("mainForm.glade")
        builder.connect_signals(self)

        # Windows
        self.mainWindow = builder.get_object("mainWindow")
        self.mainWindow.connect("delete-event", self.shutdown)
        self.mainWindow.connect("destroy", Gtk.main_quit)
        self.about_dialog = None

        # get controls
        self.notebook_main = builder.get_object("notebook_main")
        self.imgCover = builder.get_object("imgCover")
        self.imgMap = builder.get_object("imgMap")
        self.spinFreq = builder.get_object("spinFreq")
        self.spin_stream = builder.get_object("spin_stream")
        self.spinGain = builder.get_object("spinGain")
        self.spinPPM = builder.get_object("spinPPM")
        self.spinRTL = builder.get_object("spinRTL")
        self.cb_auto_gain = builder.get_object("cb_auto_gain")
        self.btn_play = builder.get_object("btn_play")
        self.btn_stop = builder.get_object("btn_stop")
        self.btn_bookmark = builder.get_object("btn_bookmark")
        self.btn_delete = builder.get_object("btn_delete")
        self.rad_map_traffic = builder.get_object("rad_map_traffic")
        self.rad_map_weather = builder.get_object("rad_map_weather")
        self.txtTitle = builder.get_object("txtTitle")
        self.txtArtist = builder.get_object("txtArtist")
        self.txtAlbum = builder.get_object("txtAlbum")
        self.lblName = builder.get_object("lblName")
        self.lblSlogan = builder.get_object("lblSlogan")
        self.lblCall = builder.get_object("lblCall")
        self.lblGain = builder.get_object("lblGain")
        self.lblBitRate = builder.get_object("lblBitRate")
        self.lblBitRate2 = builder.get_object("lblBitRate2")
        self.lblError = builder.get_object("lblError")
        self.lblMerLower = builder.get_object("lblMerLower")
        self.lblMerUpper = builder.get_object("lblMerUpper")
        self.lblBerNow = builder.get_object("lblBerNow")
        self.lblBerAvg = builder.get_object("lblBerAvg")
        self.lblBerMin = builder.get_object("lblBerMin")
        self.lblBerMax = builder.get_object("lblBerMax")
        self.lv_bookmarks = builder.get_object("lv_bookmarks")
        self.lsBookmarks = Gtk.ListStore(str, str, int)

        self.lv_bookmarks.set_model(self.lsBookmarks)
        self.lv_bookmarks.get_selection().connect("changed", self.on_lv_bookmarks_selection_changed)

    def initStreamInfo(self):
        # stream information
        self.streamInfo = {
            "Callsign": "",         # station callsign
            "Slogan": "",           # station slogan
            "Title": "",            # track title
            "Album": "",            # track album
            "Artist": "",           # track artist
            "Cover": "",            # filename of track cover
            "Logo": "",             # station logo
            "Bitrate": 0,           # current stream bit rate
            "MER": [0, 0],          # modulation error ratio: lower, upper
            "BER": [0, 0, 0, 0],    # bit error rate: current, average, min, max
            "Gain": 0               # automatic gain
        }

        self.streams = [{}, {}, {}, {}]
        self.trafficPort = -1
        self.weatherPort = -1
        self.lastType = 0

        # clear status info
        self.lblCall.set_label("")
        self.lblBitRate.set_label("")
        self.lblBitRate2.set_label("")
        self.lblError.set_label("")
        self.lblGain.set_label("")
        self.txtTitle.set_text("")
        self.txtArtist.set_text("")
        self.txtAlbum.set_text("")
        self.imgCover.clear()
        self.lblName.set_label("")
        self.lblSlogan.set_label("")
        self.lblSlogan.set_tooltip_text("")
        self.lblMerLower.set_label("")
        self.lblMerUpper.set_label("")
        self.lblBerNow.set_label("")
        self.lblBerAvg.set_label("")
        self.lblBerMin.set_label("")
        self.lblBerMax.set_label("")

    def loadSettings(self):
        # load station logos
        try:
            with open("stationLogos.json", mode="r") as f:
                self.stationLogos = json.load(f)
        except (OSError, json.decoder.JSONDecodeError):
            logging.warning("Unable to load station logo database")

        # load settings
        try:
            with open("config.json", mode="r") as f:
                config = json.load(f)

                if "MapData" in config:
                    self.mapData = config["MapData"]
                    if self.mapData["mapMode"] == 0:
                        self.rad_map_traffic.set_active(True)
                        self.rad_map_traffic.toggled()
                    elif self.mapData["mapMode"] == 1:
                        self.rad_map_weather.set_active(True)
                        self.rad_map_weather.toggled()

                if "Width" and "Height" in config:
                    self.mainWindow.resize(config["Width"], config["Height"])

                self.mainWindow.move(config["WindowX"], config["WindowY"])
                self.spinFreq.set_value(config["Frequency"])
                self.spin_stream.set_value(config["Stream"])
                self.spinGain.set_value(config["Gain"])
                self.cb_auto_gain.set_active(config["AutoGain"])
                self.spinPPM.set_value(config["PPMError"])
                self.spinRTL.set_value(config["RTL"])
                self.bookmarks = config["Bookmarks"]
                for bookmark in self.bookmarks:
                    self.lsBookmarks.append(bookmark)
        except (OSError, json.decoder.JSONDecodeError, KeyError):
            logging.warning("Unable to load config")

        # create aas directory
        self.aasDir = os.path.join(sys.path[0], "aas")
        if not os.path.isdir(self.aasDir):
            try:
                os.mkdir(self.aasDir)
            except OSError:
                logging.error("Unable to create AAS directory")
                self.aasDir = None

        # create map directory
        self.mapDir = os.path.join(sys.path[0], "map")
        if not os.path.isdir(self.mapDir):
            try:
                os.mkdir(self.mapDir)
            except OSError:
                logging.error("Unable to create map directory")
                self.mapDir = None

    def shutdown(self, *_args):
        # stop map viewer animation if it's running
        if self.mapViewer is not None and self.mapViewer.animateTimer is not None:
            self.mapViewer.animateTimer.cancel()
            self.mapViewer.animateStop = True

            while self.mapViewer.animateBusy:
                logging.debug("Animation busy - stopping")
                if self.mapViewer.animateTimer is not None:
                    self.mapViewer.animateTimer.cancel()
                time.sleep(0.25)

        self.playing = False

        # kill nrsc5 if it's running
        if self.radio:
            self.radio.stop()
            self.radio.close()
            self.radio = None

        # shut down status timer if it's running
        if self.statusTimer is not None:
            self.statusTimer.cancel()

        self.audio_queue.put(None)
        self.audio_thread.join()

        # save settings
        try:
            with open("config.json", mode="w") as f:
                winX, winY = self.mainWindow.get_position()
                width, height = self.mainWindow.get_size()
                config = {
                    "CfgVersion": "1.2.0",
                    "WindowX": winX,
                    "WindowY": winY,
                    "Width": width,
                    "Height": height,
                    "Frequency": self.spinFreq.get_value(),
                    "Stream": int(self.spin_stream.get_value()),
                    "Gain": self.spinGain.get_value(),
                    "AutoGain": self.cb_auto_gain.get_active(),
                    "PPMError": int(self.spinPPM.get_value()),
                    "RTL": int(self.spinRTL.get_value()),
                    "Bookmarks": self.bookmarks,
                    "MapData": self.mapData,
                }
                # sort bookmarks
                config["Bookmarks"].sort(key=lambda t: t[2])

                json.dump(config, f, indent=2)

            with open("stationLogos.json", mode="w") as f:
                json.dump(self.stationLogos, f, indent=2)
        except OSError:
            logging.error("Unable to save config")


class NRSC5_Map(object):
    def __init__(self, parent, callback, data):
        # setup gui
        builder = Gtk.Builder()
        builder.add_from_file("mapForm.glade")
        builder.connect_signals(self)

        self.parent = parent                                                        # parent class
        self.callback = callback                                                    # callback function
        self.data = data                                                            # map data
        self.animateTimer = None                                                    # timer used to animate weather maps
        self.animateBusy = False
        self.animateStop = False
        self.weatherMaps = parent.weatherMaps                                       # list of weather maps sorted by time
        self.mapIndex = 0                                                           # the index of the next weather map to display

        # get the controls
        self.map_window = builder.get_object("map_window")
        self.imgMap = builder.get_object("imgMap")
        self.rad_map_weather = builder.get_object("rad_map_weather")
        self.rad_map_traffic = builder.get_object("rad_map_traffic")
        self.chk_animate = builder.get_object("chk_animate")
        self.chk_scale = builder.get_object("chk_scale")
        self.spin_speed = builder.get_object("spin_speed")
        self.adjSpeed = builder.get_object("adjSpeed")
        self.imgKey = builder.get_object("imgKey")

        self.map_window.connect("delete-event", self.on_map_window_delete)

        self.config = data["viewerConfig"]                                          # get the map viewer config
        self.map_window.resize(*self.config["windowSize"])                          # set the window size
        self.map_window.move(*self.config["windowPos"])                             # set the window position
        if self.config["mode"] == 0:
            self.rad_map_traffic.set_active(True)                                   # set the map radio buttons
        elif self.config["mode"] == 1:
            self.rad_map_weather.set_active(True)
        self.setMap(self.config["mode"])                                            # display the current map

        self.chk_animate.set_active(self.config["animate"])                         # set the animation mode
        self.chk_scale.set_active(self.config["scale"])                             # set the scale mode
        self.spin_speed.set_value(self.config["animationSpeed"])                    # set the animation speed

    def on_rad_map_toggled(self, btn):
        if btn.get_active():
            if btn == self.rad_map_traffic:
                self.config["mode"] = 0
                self.imgKey.set_visible(False)                                                          # hide the key for the weather radar

                # stop animation if it's enabled
                if self.animateTimer is not None:
                    self.animateTimer.cancel()
                    self.animateTimer = None

                self.setMap(0)                                                                          # show the traffic map

            elif btn == self.rad_map_weather:
                self.config["mode"] = 1
                self.imgKey.set_visible(True)                                                           # show the key for the weather radar

                # check if animate is enabled and start animation
                if self.config["animate"] and self.animateTimer is None:
                    self.animateTimer = threading.Timer(0.05, self.animate)
                    self.animateTimer.start()

                # no animation, just show the current map
                elif not self.config["animate"]:
                    self.setMap(1)

    def on_chk_animate_toggled(self, _btn):
        self.config["animate"] = self.chk_animate.get_active()

        if self.config["animate"] and self.config["mode"] == 1:
            # start animation
            self.animateTimer = threading.Timer(self.config["animationSpeed"], self.animate)            # create the animation timer
            self.animateTimer.start()                                                                   # start the animation timer
        else:
            # stop animation
            if self.animateTimer is not None:
                self.animateTimer.cancel()                                                              # cancel the animation timer
                self.animateTimer = None
            self.mapIndex = len(self.weatherMaps)-1                                                     # reset the animation index
            self.setMap(self.config["mode"])                                                            # show the most recent map

    def on_chk_scale_toggled(self, btn):
        self.config["scale"] = btn.get_active()
        if self.config["mode"] == 1:
            if self.config["animate"]:
                i = len(self.weatherMaps)-1 if (self.mapIndex-1 < 0) else self.mapIndex-1               # get the index for the current map in the animation
                self.showImage(self.weatherMaps[i], self.config["scale"])                               # show the current map in the animation
            else:
                self.showImage(self.data["weatherNow"], self.config["scale"])                           # show the most recent map

    def on_spin_speed_value_changed(self, _spn):
        self.config["animationSpeed"] = self.adjSpeed.get_value()                                       # get the animation speed

    def on_map_window_delete(self, *_args):
        # cancel the timer if it's running
        if self.animateTimer is not None:
            self.animateTimer.cancel()
            self.animateStop = True

        # wait for animation to finish
        while self.animateBusy:
            self.parent.debugLog("Waiting for animation to finish")
            if self.animateTimer is not None:
                self.animateTimer.cancel()
            time.sleep(0.25)

        self.config["windowPos"] = self.map_window.get_position()                                       # store current window position
        self.config["windowSize"] = self.map_window.get_size()                                          # store current window size
        self.callback()                                                                                 # run the callback

    def animate(self):
        fileName = self.weatherMaps[self.mapIndex] if self.weatherMaps else ""
        if os.path.isfile(fileName):
            self.animateBusy = True                                                                     # set busy to true

            if self.config["scale"]:
                mapImg = imgToPixbuf(Image.open(fileName).resize((600, 600), Image.LANCZOS))            # open weather map, resize to 600x600, and convert to pixbuf
            else:
                mapImg = imgToPixbuf(Image.open(fileName))                                              # open weather map and convert to pixbuf

            if self.config["animate"] and self.config["mode"] == 1 and not self.animateStop:            # check if the viwer is set to animated weather map
                self.imgMap.set_from_pixbuf(mapImg)                                                     # display image
                self.mapIndex += 1                                                                      # incriment image index
                if self.mapIndex >= len(self.weatherMaps):                                              # check if this is the last image
                    self.mapIndex = 0                                                                   # reset the map index
                    self.animateTimer = threading.Timer(2, self.animate)                                # show the last image for a longer time
                else:
                    self.animateTimer = threading.Timer(self.config["animationSpeed"], self.animate)    # set the timer to the normal speed

                self.animateTimer.start()                                                               # start the timer
            else:
                self.animateTimer = None                                                                # clear the timer

            self.animateBusy = False                                                                    # set busy to false
        else:
            self.chk_animate.set_active(False)                                                          # stop animation if image was not found
            self.mapIndex = 0

    def showImage(self, fileName, scale):
        if os.path.isfile(fileName):
            if scale:
                mapImg = Image.open(fileName).resize((600, 600), Image.LANCZOS)                         # open and scale map to fit window
            else:
                mapImg = Image.open(fileName)                                                           # open map

            self.imgMap.set_from_pixbuf(imgToPixbuf(mapImg))                                            # convert image to pixbuf and display
        else:
            self.imgMap.set_from_stock(Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)             # display missing image if file is not found

    def setMap(self, map_type):
        if map_type == 0:
            self.showImage(os.path.join("map", "TrafficMap.png"), False)                                # show traffic map
        elif map_type == 1:
            self.showImage(self.data["weatherNow"], self.config["scale"])                               # show weather map

    def updated(self):
        if self.config["mode"] == 0:
            self.setMap(0)
        elif self.config["mode"] == 1:
            self.setMap(1)
            self.mapIndex = len(self.weatherMaps)-1


def dtToTs(dt):
    # convert datetime to timestamp
    return int((dt - datetime.datetime(1970, 1, 1, tzinfo=tz.tzutc())).total_seconds())


def imgToPixbuf(img):
    # convert PIL.Image to gdk.pixbuf
    with tempfile.NamedTemporaryFile("wb", suffix=".png") as f:
        img.save(f)
        return GdkPixbuf.Pixbuf.new_from_file(f.name)


if __name__ == "__main__":
    # show main window and start main thread
    os.chdir(sys.path[0])
    nrsc5_gui = NRSC5_GUI()
    nrsc5_gui.mainWindow.show()
    Gtk.main()
