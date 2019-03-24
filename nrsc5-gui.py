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

from dateutil import tz
from PIL import Image, ImageFont, ImageDraw

import datetime
import glob
import json
import logging
import nrsc5
import os
import pyaudio
import queue
import re
import shutil
import sys
import tempfile
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GObject, Gdk, GdkPixbuf


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
        self.mapData = {
            "mapMode": 1,
            "mapTiles": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
            "mapComplete": False,
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
        nameRenderer.connect("edited", self.on_bookmarkNameEdited)

        colStation = Gtk.TreeViewColumn("Station", Gtk.CellRendererText(), text=0)
        colName = Gtk.TreeViewColumn("Name", nameRenderer, text=1)

        colStation.set_resizable(True)
        colStation.set_sort_column_id(2)
        colName.set_resizable(True)
        colName.set_sort_column_id(1)

        self.lvBookmarks.append_column(colStation)
        self.lvBookmarks.append_column(colName)

        self.loadSettings()
        self.proccessWeatherMaps()

        self.audio_thread.start()

    def display_logo(self):
        if self.stationStr in self.stationLogos:
            # show station logo if it's cached
            logo = os.path.join(self.aasDir, self.stationLogos[self.stationStr][self.stationNum])
            if os.path.isfile(logo):
                self.streamInfo["Logo"] = self.stationLogos[self.stationStr][self.stationNum]
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(logo)
                pixbuf = pixbuf.scale_simple(200, 200, GdkPixbuf.InterpType.HYPER)
                self.imgCover.set_from_pixbuf(pixbuf)
        else:
            # add entry in database for the station if it doesn't exist
            self.stationLogos[self.stationStr] = ["", "", "", ""]

    def on_btnPlay_clicked(self, btn):
        # start playback
        if not self.playing:

            # update all of the spin buttons to prevent the text from sticking
            self.spinFreq.update()
            self.spinStream.update()
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
            self.btnPlay.set_sensitive(False)
            self.btnStop.set_sensitive(True)
            self.cbAutoGain.set_sensitive(False)
            self.playing = True
            self.lastXHDR = ""

            self.play()

            self.stationStr = str(self.spinFreq.get_value())
            self.stationNum = int(self.spinStream.get_value())-1

            self.display_logo()

            # check if station is bookmarked
            self.bookmarked = False
            freq = int((self.spinFreq.get_value()+0.005)*100) + int(self.spinStream.get_value())
            for b in self.bookmarks:
                if b[2] == freq:
                    self.bookmarked = True
                    break

            self.btnBookmark.set_sensitive(not self.bookmarked)
            if self.notebookMain.get_current_page() != 3:
                self.btnDelete.set_sensitive(self.bookmarked)

    def on_btnStop_clicked(self, btn):
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
            if not self.cbAutoGain.get_active():
                self.spinGain.set_sensitive(True)
            self.spinFreq.set_sensitive(True)
            self.spinPPM.set_sensitive(True)
            self.spinRTL.set_sensitive(True)
            self.btnPlay.set_sensitive(True)
            self.btnStop.set_sensitive(False)
            self.btnBookmark.set_sensitive(False)
            self.cbAutoGain.set_sensitive(True)

            # clear stream info
            self.initStreamInfo()

            self.btnBookmark.set_sensitive(False)
            if self.notebookMain.get_current_page() != 3:
                self.btnDelete.set_sensitive(False)

    def on_btnBookmark_clicked(self, btn):
        # pack frequency and channel number into one int
        freq = int((self.spinFreq.get_value()+0.005)*100) + int(self.spinStream.get_value())

        # create bookmark
        bookmark = [
            "{:4.1f}-{:1.0f}".format(self.spinFreq.get_value(), self.spinStream.get_value()),
            self.streamInfo["Callsign"],
            freq
        ]
        self.bookmarked = True                  # mark as bookmarked
        self.bookmarks.append(bookmark)         # store bookmark in array
        self.lsBookmarks.append(bookmark)       # add bookmark to listview
        self.btnBookmark.set_sensitive(False)   # disable bookmark button

        if self.notebookMain.get_current_page() != 3:
            self.btnDelete.set_sensitive(True)  # enable delete button

    def on_btnDelete_clicked(self, btn):
        # select current station if not on bookmarks page
        if self.notebookMain.get_current_page() != 3:
            station = int((self.spinFreq.get_value()+0.005)*100) + int(self.spinStream.get_value())
            for i in range(len(self.lsBookmarks)):
                if self.lsBookmarks[i][2] == station:
                    self.lvBookmarks.set_cursor(i)
                    break

        # get station of selected row
        (model, iter) = self.lvBookmarks.get_selection().get_selected()
        station = model.get_value(iter, 2)

        # remove row
        model.remove(iter)

        # remove bookmark
        for i in range(len(self.bookmarks)):
            if self.bookmarks[i][2] == station:
                self.bookmarks.pop(i)
                break

        if self.notebookMain.get_current_page() != 3 and self.playing:
            self.btnBookmark.set_sensitive(True)
            self.bookmarked = False

    def on_btnAbout_activate(self, btn):
        # sets up and displays about dialog
        if self.about_dialog:
            self.about_dialog.present()
            return

        authors = [
            "Cody Nybo <cmnybo@gmail.com>",
            "Clayton Smith <argilo@gmail.com>",
        ]

        license = """
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
        about_dialog.set_license(license)
        about_dialog.set_logo(GdkPixbuf.Pixbuf.new_from_file("logo.png"))

        # callbacks for destroying the dialog
        def close(dialog, response, editor):
            editor.about_dialog = None
            dialog.destroy()

        def delete_event(dialog, event, editor):
            editor.about_dialog = None
            return True

        about_dialog.connect("response", close, self)
        about_dialog.connect("delete-event", delete_event, self)

        self.about_dialog = about_dialog
        about_dialog.show()

    def on_spinStream_value_changed(self, spin):
        self.lastXHDR = ""
        self.streamInfo["Title"] = ""
        self.streamInfo["Album"] = ""
        self.streamInfo["Artist"] = ""
        self.streamInfo["Cover"] = ""
        self.streamInfo["Logo"] = ""
        self.streamInfo["Bitrate"] = 0
        self.stationNum = int(self.spinStream.get_value())-1
        if self.playing:
            self.display_logo()

    def on_cbAutoGain_toggled(self, btn):
        self.spinGain.set_sensitive(not btn.get_active())
        self.lblGain.set_visible(btn.get_active())

    def on_listviewBookmarks_row_activated(self, treeview, path, view_column):
        if len(path) != 0:
            # get station from bookmark row
            tree_iter = treeview.get_model().get_iter(path[0])
            station = treeview.get_model().get_value(tree_iter, 2)

            # set frequency and stream
            self.spinFreq.set_value(float(int(station/10)/10.0))
            self.spinStream.set_value(station % 10)

            # stop playback if playing
            if self.playing:
                self.on_btnStop_clicked(None)

            # play bookmarked station
            self.on_btnPlay_clicked(None)

    def on_lvBookmarks_selection_changed(self, tree_selection):
        # enable delete button if bookmark is selected
        (model, pathlist) = self.lvBookmarks.get_selection().get_selected_rows()
        self.btnDelete.set_sensitive(len(pathlist) != 0)

    def on_bookmarkNameEdited(self, cell, path, text, data=None):
        # update name in listview
        iter = self.lsBookmarks.get_iter(path)
        self.lsBookmarks.set(iter, 1, text)

        # update name in bookmarks array
        for b in self.bookmarks:
            if b[2] == self.lsBookmarks[path][2]:
                b[1] = text
                break

    def on_notebookMain_switch_page(self, notebook, page, page_num):
        # disable delete button if not on bookmarks page and station is not bookmarked
        if page_num != 3 and (not self.bookmarked or not self.playing):
            self.btnDelete.set_sensitive(False)
        # enable delete button if not on bookmarks page and station is bookmarked
        elif page_num != 3 and self.bookmarked:
            self.btnDelete.set_sensitive(True)
        # enable delete button if on bookmarks page and a bookmark is selected
        else:
            (model, iter) = self.lvBookmarks.get_selection().get_selected()
            self.btnDelete.set_sensitive(iter is not None)

    def on_radMap_toggled(self, btn):
        if btn.get_active():
            if btn == self.radMapTraffic:
                self.mapData["mapMode"] = 0
                mapFile = os.path.join("map", "TrafficMap.png")
                if os.path.isfile(mapFile):                                                             # check if map exists
                    mapImg = Image.open(mapFile).resize((200, 200), Image.LANCZOS)                      # scale map to fit window
                    self.imgMap.set_from_pixbuf(imgToPixbuf(mapImg))                                    # convert image to pixbuf and display
                else:
                    self.imgMap.set_from_stock(Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)     # display missing image if file is not found

            elif btn == self.radMapWeather:
                self.mapData["mapMode"] = 1
                if os.path.isfile(self.mapData["weatherNow"]):
                    mapImg = Image.open(self.mapData["weatherNow"]).resize((200, 200), Image.LANCZOS)   # scale map to fit window
                    self.imgMap.set_from_pixbuf(imgToPixbuf(mapImg))                                    # convert image to pixbuf and display
                else:
                    self.imgMap.set_from_stock(Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)     # display missing image if file is not found

    def on_btnMap_clicked(self, btn):
        # open map viewer window
        if self.mapViewer is None:
            self.mapViewer = NRSC5_Map(self, self.mapViewerCallback, self.mapData)
            self.mapViewer.mapWindow.show()

    def mapViewerCallback(self):
        # delete the map viewer
        self.mapViewer = None

    def play(self):
        self.radio = nrsc5.NRSC5(lambda type, evt: self.callback(type, evt))
        self.radio.open(int(self.spinRTL.get_value()), int(self.spinPPM.get_value()))
        self.radio.set_auto_gain(self.cbAutoGain.get_active())

        # set gain if auto gain is not selected
        if not self.cbAutoGain.get_active():
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

                if self.cbAutoGain.get_active():
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

    def processTrafficMap(self, fileName):
        r = re.compile("^TMT_.*_([1-3])_([1-3])_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2}).*$")               # match file name
        m = r.match(fileName)

        if m:
            x = int(m.group(1))-1  # X position
            y = int(m.group(2))-1  # Y position

            # get time from map tile and convert to local time
            dt = datetime.datetime(int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7)), tzinfo=tz.tzutc())
            t = dt.astimezone(tz.tzlocal())                                                             # local time
            ts = dtToTs(dt)                                                                             # unix timestamp (utc)

            # check if the tile has already been loaded
            if self.mapData["mapTiles"][x][y] == ts:
                try:
                    os.remove(os.path.join("aas", fileName))                                            # delete this tile, it's not needed
                except OSError:
                    pass
                return                                                                                  # no need to recreate the map if it hasn't changed

            logging.debug("Got traffic map tile: {}, {}".format(x, y))

            self.mapData["mapComplete"] = False                                                         # new tiles are coming in, the map is nolonger complete
            self.mapData["mapTiles"][x][y] = ts                                                         # store time for current tile

            try:
                currentPath = os.path.join("aas", fileName)                                             # create path to map tile
                newPath = os.path.join("map", "TrafficMap_{:g}_{:g}.png".format(x, y))                  # create path to new tile location
                if(os.path.exists(newPath)):
                    os.remove(newPath)                                                                  # delete old image if it exists (only necessary on windows)
                shutil.move(currentPath, newPath)                                                       # move and rename map tile
            except OSError:
                logging.error("Error moving map tile")
                self.mapData["mapTiles"][x][y] = 0

            # check if all of the tiles are loaded
            if self.checkTiles(ts):
                logging.debug("Got complete traffic map")
                self.mapData["mapComplete"] = True                                                      # map is complete

                # stitch the map tiles into one image
                imgMap = Image.new("RGB", (600, 600), "white")                                          # create blank image for traffic map
                for i in range(3):
                    for j in range(3):
                        tileFile = os.path.join("map", "TrafficMap_{:g}_{:g}.png".format(i, j))         # get path to tile
                        imgMap.paste(Image.open(tileFile), (j*200, i*200))                              # paste tile into map
                        os.remove(tileFile)                                                             # delete tile image

                imgMap.save(os.path.join("map", "TrafficMap.png"))                                      # save traffic map

                # display on map page
                if self.radMapTraffic.get_active():
                    imgMap = imgMap.resize((200, 200), Image.LANCZOS)                                   # scale map to fit window
                    self.imgMap.set_from_pixbuf(imgToPixbuf(imgMap))                                    # convert image to pixbuf and display

                if self.mapViewer is not None:
                    self.mapViewer.updated(0)                                                           # notify map viwerer if it's open

    def processWeatherOverlay(self, fileName):
        r = re.compile("^DWRO_(.*)_.*_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2}).*$")                         # match file name
        m = r.match(fileName)

        if m:
            # get time from map tile and convert to local time
            dt = datetime.datetime(int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)), tzinfo=tz.tzutc())
            t = dt.astimezone(tz.tzlocal())                                                             # local time
            ts = dtToTs(dt)                                                                             # unix timestamp (utc)
            id = self.mapData["weatherID"]

            if m.group(1) != id:
                logging.error("Received weather overlay with the wrong ID: " + m.group(1))
                return

            if self.mapData["weatherTime"] == ts:
                try:
                    os.remove(os.path.join("aas", fileName))                                            # delete this tile, it's not needed
                except OSError:
                    pass
                return                                                                                  # no need to recreate the map if it hasn't changed

            logging.debug("Got weather overlay")

            self.mapData["weatherTime"] = ts                                                            # store time for current overlay
            wxOlPath = os.path.join("map", "WeatherOverlay_{:s}_{:}.png".format(id, ts))
            wxMapPath = os.path.join("map", "WeatherMap_{:s}_{:}.png".format(id, ts))

            # move new overlay to map directory
            try:
                if(os.path.exists(wxOlPath)):
                    os.remove(wxOlPath)                                                                 # delete old image if it exists (only necessary on windows)
                shutil.move(os.path.join("aas", fileName), wxOlPath)                                    # move and rename map tile
            except OSError:
                logging.error("Error moving weather overlay")
                self.mapData["weatherTime"] = 0

            # create weather map
            try:
                mapPath = os.path.join("map", "BaseMap_" + id + ".png")                                 # get path to base map
                if not os.path.isfile(mapPath):                                                         # make sure base map exists
                    self.makeBaseMap(self.mapData["weatherID"], self.mapData["weatherPos"])             # create base map if it doesn't exist

                imgMap = Image.open(mapPath).convert("RGBA")                                            # open map image
                posTS = (imgMap.size[0]-235, imgMap.size[1]-29)                                         # calculate position to put timestamp (bottom right)
                imgTS = self.mkTimestamp(t, imgMap.size, posTS)                                         # create timestamp
                imgRadar = Image.open(wxOlPath).convert("RGBA")                                         # open radar overlay
                imgRadar = imgRadar.resize(imgMap.size, Image.LANCZOS)                                  # resize radar overlay to fit the map
                imgMap = Image.alpha_composite(imgMap, imgRadar)                                        # overlay radar image on map
                imgMap = Image.alpha_composite(imgMap, imgTS)                                           # overlay timestamp
                imgMap.save(wxMapPath)                                                                  # save weather map
                os.remove(wxOlPath)                                                                     # remove overlay image
                self.mapData["weatherNow"] = wxMapPath

                # display on map page
                if self.radMapWeather.get_active():
                    imgMap = imgMap.resize((200, 200), Image.LANCZOS)                                   # scale map to fit window
                    self.imgMap.set_from_pixbuf(imgToPixbuf(imgMap))                                    # convert image to pixbuf and display

                self.proccessWeatherMaps()                                                              # get rid of old maps and add new ones to the list
                if self.mapViewer is not None:
                    self.mapViewer.updated(1)                                                           # notify map viwerer if it's open

            except OSError:
                logging.error("Error creating weather map")
                self.mapData["weatherTime"] = 0

    def proccessWeatherInfo(self, fileName):
        weatherID = None
        weatherPos = None

        try:
            with open(os.path.join("aas", fileName)) as weatherInfo:                                    # open weather info file
                for line in weatherInfo:                                                                # read line by line
                    if "DWR_Area_ID=" in line:                                                          # look for line with "DWR_Area_ID=" in it
                        # get ID from line
                        r = re.compile("^DWR_Area_ID=\"(.+)\"$")
                        m = r.match(line)
                        weatherID = m.group(1)

                    elif "Coordinates=" in line:                                                        # look for line with "Coordinates=" in it
                        # get coordinates from line
                        r = re.compile("^Coordinates=.*\((-?\d+\.\d+),(-?\d+\.\d+)\).*\((-?\d+\.\d+),(-?\d+\.\d+)\).*$")
                        m = r.match(line)
                        weatherPos = [float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))]
        except OSError:
            logging.error("Error opening weather info")

        if weatherID is not None and weatherPos is not None:                                            # check if ID and position were found
            if self.mapData["weatherID"] != weatherID or self.mapData["weatherPos"] != weatherPos:      # check if ID or position has changed
                logging.debug("Got position: ({:n}, {:n}) ({:n}, {:n})".format(*weatherPos))
                self.mapData["weatherID"] = weatherID                                                   # set weather ID
                self.mapData["weatherPos"] = weatherPos                                                 # set weather map position

                self.makeBaseMap(weatherID, weatherPos)
                self.weatherMaps = []
                self.proccessWeatherMaps()

    def proccessWeatherMaps(self):
        numberOfMaps = 0
        r = re.compile("^map.WeatherMap_([a-zA-Z0-9]+)_([0-9]+).png")
        now = dtToTs(datetime.datetime.now(tz.tzutc()))                                                 # get current time
        files = glob.glob(os.path.join("map", "WeatherMap_") + "*.png")                                 # look for weather map files
        files.sort()                                                                                    # sort files
        for f in files:
            m = r.match(f)                                                                              # match regex
            if m:
                id = m.group(1)                                                                         # location ID
                ts = int(m.group(2))                                                                    # timestamp (UTC)

                # remove weather maps older than 12 hours
                if now - ts > 60*60*12:
                    try:
                        if f in self.weatherMaps:
                            self.weatherMaps.pop(self.weatherMaps.index(f))                             # remove from list
                        os.remove(f)                                                                    # remove file
                        logging.debug("Deleted old weather map: " + f)
                    except OSError:
                        logging.error("Failed to delete old weather map: " + f)

                # skip if not the correct location
                elif id == self.mapData["weatherID"]:
                    if f not in self.weatherMaps:
                        self.weatherMaps.append(f)                                                      # add to list
                    numberOfMaps += 1

        logging.debug("Found {} weather maps".format(numberOfMaps))

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

    def makeBaseMap(self, id, pos):
        mapPath = os.path.join("map", "BaseMap_" + id + ".png")                                 # get map path
        if os.path.isfile(self.MAP_FILE):
            if not os.path.isfile(mapPath):                                                     # check if the map has already been created for this location
                logging.debug("Creating new map: " + mapPath)
                px = self.getMapArea(*pos)                                                      # convert map locations to pixel coordinates
                mapImg = Image.open(self.MAP_FILE).crop(px)                                     # open the full map and crop it to the coordinates
                mapImg.save(mapPath)                                                            # save the cropped map to disk for later use
                logging.debug("Finished creating map")
        else:
            logging.error("Map file not found: " + self.MAP_FILE)
            mapImg = Image.new("RGBA", (pos[2]-pos[1], pos[3]-pos[1]), "white")                 # if the full map is not available, use a blank image
            mapImg.save(mapPath)

    def checkTiles(self, t):
        # check if all the tiles have been received
        for i in range(3):
            for j in range(3):
                if self.mapData["mapTiles"][i][j] != t:
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
            logging.warn("No audio output device available")
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

    def callback(self, type, evt):
        if type == nrsc5.EventType.LOST_DEVICE:
            pass  # TODO: update the GUI?
        elif type == nrsc5.EventType.SYNC:
            self.streamInfo["Gain"] = self.radio.get_gain()
            # TODO: update the GUI?
        elif type == nrsc5.EventType.LOST_SYNC:
            pass  # TODO: update the GUI?
        elif type == nrsc5.EventType.MER:
            self.streamInfo["MER"] = [evt.lower, evt.upper]
        elif type == nrsc5.EventType.BER:
            self.update_ber(evt.cber)
        elif type == nrsc5.EventType.HDC:
            if evt.program == self.stationNum:
                self.update_bitrate(len(evt.data) * 8)
        elif type == nrsc5.EventType.AUDIO:
            if evt.program == self.stationNum:
                self.audio_queue.put(evt.data)
        elif type == nrsc5.EventType.ID3:
            if evt.program == self.stationNum:
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
                        logging.debug("XHDR changed: {}".format(evt.xhdr.param))
        elif type == nrsc5.EventType.SIG:
            for service in evt:
                logging.debug("Found stream: type {}, number {}". format(service.type, service.number))
                if service.type == nrsc5.ServiceType.AUDIO:
                    for component in service.components:
                        if component.type == nrsc5.ComponentType.DATA:
                            logging.debug("    Found port: {:04X}". format(component.data.port))
                            self.streams[service.number-1].append(component.data.port)
        elif type == nrsc5.EventType.LOT:
            if self.aasDir:
                path = os.path.join(self.aasDir, evt.name)
                with open(path, "wb") as f:
                    f.write(evt.data)

                if evt.port == self.streams[self.stationNum][0]:
                    self.streamInfo["Cover"] = evt.name
                    logging.debug("Got album cover: " + evt.name)
                elif evt.port == self.streams[self.stationNum][1]:
                    self.streamInfo["Logo"] = evt.name
                    self.stationLogos[self.stationStr][self.stationNum] = evt.name                # add station logo to database
                    logging.debug("Got station logo: " + evt.name)
                elif evt.name[0:5] == "DWRO_" and self.mapDir is not None:
                    self.processWeatherOverlay(evt.name)
                elif evt.name[0:4] == "TMT_" and self.mapDir is not None:
                    self.processTrafficMap(evt.name)                                              # proccess traffic map tile
                elif evt.name[0:5] == "DWRI_" and self.mapDir is not None:
                    self.proccessWeatherInfo(evt.name)

        elif type == nrsc5.EventType.SIS:
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
        self.notebookMain = builder.get_object("notebookMain")
        self.imgCover = builder.get_object("imgCover")
        self.imgMap = builder.get_object("imgMap")
        self.spinFreq = builder.get_object("spinFreq")
        self.spinStream = builder.get_object("spinStream")
        self.spinGain = builder.get_object("spinGain")
        self.spinPPM = builder.get_object("spinPPM")
        self.spinRTL = builder.get_object("spinRTL")
        self.cbAutoGain = builder.get_object("cbAutoGain")
        self.btnPlay = builder.get_object("btnPlay")
        self.btnStop = builder.get_object("btnStop")
        self.btnBookmark = builder.get_object("btnBookmark")
        self.btnDelete = builder.get_object("btnDelete")
        self.radMapTraffic = builder.get_object("radMapTraffic")
        self.radMapWeather = builder.get_object("radMapWeather")
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
        self.lvBookmarks = builder.get_object("listviewBookmarks")
        self.lsBookmarks = Gtk.ListStore(str, str, int)

        self.lvBookmarks.set_model(self.lsBookmarks)
        self.lvBookmarks.get_selection().connect("changed", self.on_lvBookmarks_selection_changed)

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

        self.streams = [[], [], [], []]
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
            logging.warn("Unable to load station logo database")

        # load settings
        try:
            with open("config.json", mode="r") as f:
                config = json.load(f)

                if "MapData" in config:
                    self.mapData = config["MapData"]
                    if self.mapData["mapMode"] == 0:
                        self.radMapTraffic.set_active(True)
                        self.radMapTraffic.toggled()
                    elif self.mapData["mapMode"] == 1:
                        self.radMapWeather.set_active(True)
                        self.radMapWeather.toggled()

                if "Width" and "Height" in config:
                    self.mainWindow.resize(config["Width"], config["Height"])

                self.mainWindow.move(config["WindowX"], config["WindowY"])
                self.spinFreq.set_value(config["Frequency"])
                self.spinStream.set_value(config["Stream"])
                self.spinGain.set_value(config["Gain"])
                self.cbAutoGain.set_active(config["AutoGain"])
                self.spinPPM.set_value(config["PPMError"])
                self.spinRTL.set_value(config["RTL"])
                self.bookmarks = config["Bookmarks"]
                for bookmark in self.bookmarks:
                    self.lsBookmarks.append(bookmark)
        except (OSError, json.decoder.JSONDecodeError, KeyError):
            logging.warn("Unable to load config")

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

    def shutdown(self, *args):
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
                    "CfgVersion": "1.1.0",
                    "WindowX": winX,
                    "WindowY": winY,
                    "Width": width,
                    "Height": height,
                    "Frequency": self.spinFreq.get_value(),
                    "Stream": int(self.spinStream.get_value()),
                    "Gain": self.spinGain.get_value(),
                    "AutoGain": self.cbAutoGain.get_active(),
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
        self.mapWindow = builder.get_object("mapWindow")
        self.imgMap = builder.get_object("imgMap")
        self.radMapWeather = builder.get_object("radMapWeather")
        self.radMapTraffic = builder.get_object("radMapTraffic")
        self.chkAnimate = builder.get_object("chkAnimate")
        self.chkScale = builder.get_object("chkScale")
        self.spnSpeed = builder.get_object("spnSpeed")
        self.adjSpeed = builder.get_object("adjSpeed")
        self.imgKey = builder.get_object("imgKey")

        self.mapWindow.connect("delete-event", self.on_mapWindow_delete)

        self.config = data["viewerConfig"]                                          # get the map viewer config
        self.mapWindow.resize(*self.config["windowSize"])                           # set the window size
        self.mapWindow.move(*self.config["windowPos"])                              # set the window position
        if self.config["mode"] == 0:
            self.radMapTraffic.set_active(True)                                     # set the map radio buttons
        elif self.config["mode"] == 1:
            self.radMapWeather.set_active(True)
        self.setMap(self.config["mode"])                                            # display the current map

        self.chkAnimate.set_active(self.config["animate"])                          # set the animation mode
        self.chkScale.set_active(self.config["scale"])                              # set the scale mode
        self.spnSpeed.set_value(self.config["animationSpeed"])                      # set the animation speed

    def on_radMap_toggled(self, btn):
        if btn.get_active():
            if btn == self.radMapTraffic:
                self.config["mode"] = 0
                self.imgKey.set_visible(False)                                                          # hide the key for the weather radar

                # stop animation if it's enabled
                if self.animateTimer is not None:
                    self.animateTimer.cancel()
                    self.animateTimer = None

                self.setMap(0)                                                                          # show the traffic map

            elif btn == self.radMapWeather:
                self.config["mode"] = 1
                self.imgKey.set_visible(True)                                                           # show the key for the weather radar

                # check if animate is enabled and start animation
                if self.config["animate"] and self.animateTimer is None:
                    self.animateTimer = threading.Timer(0.05, self.animate)
                    self.animateTimer.start()

                # no animation, just show the current map
                elif not self.config["animate"]:
                    self.setMap(1)

    def on_chkAnimate_toggled(self, btn):
        self.config["animate"] = self.chkAnimate.get_active()

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

    def on_chkScale_toggled(self, btn):
        self.config["scale"] = btn.get_active()
        if self.config["mode"] == 1:
            if self.config["animate"]:
                i = len(self.weatherMaps)-1 if (self.mapIndex-1 < 0) else self.mapIndex-1               # get the index for the current map in the animation
                self.showImage(self.weatherMaps[i], self.config["scale"])                               # show the current map in the animation
            else:
                self.showImage(self.data["weatherNow"], self.config["scale"])                           # show the most recent map

    def on_spnSpeed_value_changed(self, spn):
        self.config["animationSpeed"] = self.adjSpeed.get_value()                                       # get the animation speed

    def on_mapWindow_delete(self, *args):
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

        self.config["windowPos"] = self.mapWindow.get_position()                                        # store current window position
        self.config["windowSize"] = self.mapWindow.get_size()                                           # store current window size
        self.callback()                                                                                 # run the callback

    def animate(self):
        fileName = self.weatherMaps[self.mapIndex] if len(self.weatherMaps) else ""
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
            self.chkAnimate.set_active(False)                                                           # stop animation if image was not found
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

    def setMap(self, map):
        if map == 0:
            self.showImage(os.path.join("map", "TrafficMap.png"), False)                                # show traffic map
        elif map == 1:
            self.showImage(self.data["weatherNow"], self.config["scale"])                               # show weather map

    def updated(self, imageType):
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
