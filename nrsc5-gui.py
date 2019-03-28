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

    logLevel = 20  # decrease to 10 to enable debug logs

    def __init__(self):
        logging.basicConfig(level=self.logLevel,
                            format="%(asctime)s %(levelname)-5s %(filename)s:%(lineno)d: %(message)s",
                            datefmt="%H:%M:%S")

        GObject.threads_init()

        self.getControls()  # get controls and windows
        self.initStreamInfo()  # initilize stream info and clear status widgets

        self.radio = None
        self.audio_queue = queue.Queue(maxsize=64)
        self.audio_thread = threading.Thread(target=self.audio_worker)
        self.playing = False
        self.status_timer = None
        self.image_changed = False
        self.xhdr_changed = False
        self.last_image = ""
        self.last_xhdr = ""
        self.station_str = ""  # current station frequency (string)
        self.stream_num = 0
        self.bookmarks = []
        self.station_logos = {}
        self.bookmarked = False
        self.map_viewer = None
        self.weather_maps = []  # list of current weathermaps sorted by time
        self.traffic_map = Image.new("RGB", (600, 600), "white")
        self.map_tiles = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        self.map_data = {
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
        name_renderer = Gtk.CellRendererText()
        name_renderer.set_property("editable", True)
        name_renderer.connect("edited", self.on_bookmark_name_edited)

        col_station = Gtk.TreeViewColumn("Station", Gtk.CellRendererText(), text=0)
        col_name = Gtk.TreeViewColumn("Name", name_renderer, text=1)

        col_station.set_resizable(True)
        col_station.set_sort_column_id(2)
        col_name.set_resizable(True)
        col_name.set_sort_column_id(1)

        self.lv_bookmarks.append_column(col_station)
        self.lv_bookmarks.append_column(col_name)

        self.loadSettings()
        self.processWeatherMaps()

        self.audio_thread.start()

    def display_logo(self):
        if self.station_str in self.station_logos:
            # show station logo if it's cached
            logo = os.path.join(self.aas_dir, self.station_logos[self.station_str][self.stream_num])
            if os.path.isfile(logo):
                self.stream_info["Logo"] = self.station_logos[self.station_str][self.stream_num]
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(logo)
                pixbuf = pixbuf.scale_simple(200, 200, GdkPixbuf.InterpType.HYPER)
                self.img_cover.set_from_pixbuf(pixbuf)
        else:
            # add entry in database for the station if it doesn't exist
            self.station_logos[self.station_str] = ["", "", "", ""]

    def on_btn_play_clicked(self, _btn):
        # start playback
        if not self.playing:

            # update all of the spin buttons to prevent the text from sticking
            self.spin_freq.update()
            self.spin_stream.update()
            self.spin_gain.update()
            self.spin_ppm.update()
            self.spin_rtl.update()

            # start the timer
            self.status_timer = threading.Timer(1, self.checkStatus)
            self.status_timer.start()

            # disable the controls
            self.spin_freq.set_sensitive(False)
            self.spin_gain.set_sensitive(False)
            self.spin_ppm.set_sensitive(False)
            self.spin_rtl.set_sensitive(False)
            self.btn_play.set_sensitive(False)
            self.btn_stop.set_sensitive(True)
            self.cb_auto_gain.set_sensitive(False)
            self.playing = True
            self.last_xhdr = ""

            self.play()

            self.station_str = str(self.spin_freq.get_value())
            self.stream_num = int(self.spin_stream.get_value())-1

            self.display_logo()

            # check if station is bookmarked
            self.bookmarked = False
            freq = int((self.spin_freq.get_value()+0.005)*100) + int(self.spin_stream.get_value())
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
            self.status_timer.cancel()
            self.status_timer = None

            # enable controls
            if not self.cb_auto_gain.get_active():
                self.spin_gain.set_sensitive(True)
            self.spin_freq.set_sensitive(True)
            self.spin_ppm.set_sensitive(True)
            self.spin_rtl.set_sensitive(True)
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
        freq = int((self.spin_freq.get_value()+0.005)*100) + int(self.spin_stream.get_value())

        # create bookmark
        bookmark = [
            "{:4.1f}-{:1.0f}".format(self.spin_freq.get_value(), self.spin_stream.get_value()),
            self.stream_info["Callsign"],
            freq
        ]
        self.bookmarked = True
        self.bookmarks.append(bookmark)
        self.lsBookmarks.append(bookmark)
        self.btn_bookmark.set_sensitive(False)

        if self.notebook_main.get_current_page() != 3:
            self.btn_delete.set_sensitive(True)

    def on_btn_delete_clicked(self, _btn):
        # select current station if not on bookmarks page
        if self.notebook_main.get_current_page() != 3:
            station = int((self.spin_freq.get_value()+0.005)*100) + int(self.spin_stream.get_value())
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
        about_dialog.set_transient_for(self.main_window)
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
        self.last_xhdr = ""
        self.stream_info["Title"] = ""
        self.stream_info["Album"] = ""
        self.stream_info["Artist"] = ""
        self.stream_info["Cover"] = ""
        self.stream_info["Logo"] = ""
        self.stream_info["Bitrate"] = 0
        self.stream_num = int(self.spin_stream.get_value())-1
        if self.playing:
            self.display_logo()

    def on_cb_auto_gain_toggled(self, btn):
        self.spin_gain.set_sensitive(not btn.get_active())
        self.lbl_gain.set_visible(btn.get_active())

    def on_lv_bookmarks_row_activated(self, treeview, path, _view_column):
        if path:
            # get station from bookmark row
            tree_iter = treeview.get_model().get_iter(path[0])
            station = treeview.get_model().get_value(tree_iter, 2)

            # set frequency and stream
            self.spin_freq.set_value(float(int(station/10)/10.0))
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
                self.map_data["mapMode"] = 0
                mapFile = os.path.join("map", "TrafficMap.png")
                if os.path.isfile(mapFile):
                    map_img = Image.open(mapFile).resize((200, 200), Image.LANCZOS)
                    self.img_map.set_from_pixbuf(imgToPixbuf(map_img))
                else:
                    self.img_map.set_from_stock(Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)

            elif btn == self.rad_map_weather:
                self.map_data["mapMode"] = 1
                if os.path.isfile(self.map_data["weatherNow"]):
                    map_img = Image.open(self.map_data["weatherNow"]).resize((200, 200), Image.LANCZOS)
                    self.img_map.set_from_pixbuf(imgToPixbuf(map_img))
                else:
                    self.img_map.set_from_stock(Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)

    def on_btn_map_clicked(self, _btn):
        # open map viewer window
        if self.map_viewer is None:
            self.map_viewer = NRSC5_Map(self, self.map_viewerCallback, self.map_data)
            self.map_viewer.map_window.show()

    def map_viewerCallback(self):
        # delete the map viewer
        self.map_viewer = None

    def play(self):
        self.radio = nrsc5.NRSC5(lambda type, evt: self.callback(type, evt))
        self.radio.open(int(self.spin_rtl.get_value()), int(self.spin_ppm.get_value()))
        self.radio.set_auto_gain(self.cb_auto_gain.get_active())

        # set gain if auto gain is not selected
        if not self.cb_auto_gain.get_active():
            self.stream_info["Gain"] = self.spin_gain.get_value()
            self.radio.set_gain(self.stream_info["Gain"])

        self.radio.set_frequency(self.spin_freq.get_value() * 1e6)
        self.radio.start()

    def checkStatus(self):
        # update status information
        def update():
            Gdk.threads_enter()
            try:
                imagePath = ""
                image = ""
                ber = [self.stream_info["BER"][i]*100 for i in range(4)]
                self.txt_title.set_text(self.stream_info["Title"])
                self.txt_artist.set_text(self.stream_info["Artist"])
                self.txt_album.set_text(self.stream_info["Album"])
                self.lbl_bitrate.set_label("{:3.1f} kbps".format(self.stream_info["Bitrate"]))
                self.lbl_bitrate2.set_label("{:3.1f} kbps".format(self.stream_info["Bitrate"]))
                self.lbl_error.set_label("{:2.2f}% Error ".format(ber[1]))
                self.lbl_callsign.set_label(" " + self.stream_info["Callsign"])
                self.lbl_name.set_label(self.stream_info["Callsign"])
                self.lbl_slogan.set_label(self.stream_info["Slogan"])
                self.lbl_slogan.set_tooltip_text(self.stream_info["Slogan"])
                self.lbl_mer_lower.set_label("{:1.2f} dB".format(self.stream_info["MER"][0]))
                self.lbl_mer_upper.set_label("{:1.2f} dB".format(self.stream_info["MER"][1]))
                self.lbl_ber_now.set_label("{:1.3f}% (Now)".format(ber[0]))
                self.lbl_ber_avg.set_label("{:1.3f}% (Avg)".format(ber[1]))
                self.lbl_ber_min.set_label("{:1.3f}% (Min)".format(ber[2]))
                self.lbl_ber_max.set_label("{:1.3f}% (Max)".format(ber[3]))

                if self.cb_auto_gain.get_active():
                    self.spin_gain.set_value(self.stream_info["Gain"])
                    self.lbl_gain.set_label("{:2.1f}dB".format(self.stream_info["Gain"]))

                if self.last_xhdr == 0:
                    imagePath = os.path.join(self.aas_dir, self.stream_info["Cover"])
                    image = self.stream_info["Cover"]
                elif self.last_xhdr == 1:
                    imagePath = os.path.join(self.aas_dir, self.stream_info["Logo"])
                    image = self.stream_info["Logo"]
                    if not os.path.isfile(imagePath):
                        self.img_cover.clear()

                # resize and display image if it changed and exists
                if self.xhdr_changed and self.last_image != image and os.path.isfile(imagePath):
                    self.xhdr_changed = False
                    self.last_image = image
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(imagePath)
                    pixbuf = pixbuf.scale_simple(200, 200, GdkPixbuf.InterpType.HYPER)
                    self.img_cover.set_from_pixbuf(pixbuf)
                    logging.debug("Image changed")
            finally:
                Gdk.threads_leave()

        if self.playing:
            GObject.idle_add(update)
            self.status_timer = threading.Timer(1, self.checkStatus)
            self.status_timer.start()

    def processTrafficMap(self, filename, data):
        r = re.compile(r"^TMT_.*_([1-3])_([1-3])_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2}).*$")
        m = r.match(filename)

        if m:
            x = int(m.group(1))-1
            y = int(m.group(2))-1

            # get time from map tile and convert to local time
            dt = datetime.datetime(int(m.group(3)), int(m.group(4)), int(m.group(5)),
                                   int(m.group(6)), int(m.group(7)), tzinfo=tz.tzutc())
            ts = dtToTs(dt)

            # check if the tile has already been loaded
            if self.map_tiles[x][y] == ts:
                return  # no need to recreate the map if it hasn't changed

            logging.debug("Got traffic map tile: %s, %s", x, y)

            self.map_tiles[x][y] = ts
            self.traffic_map.paste(Image.open(io.BytesIO(data)), (y*200, x*200))

            # check if all of the tiles are loaded
            if self.checkTiles(ts):
                logging.debug("Got complete traffic map")
                self.traffic_map.save(os.path.join("map", "TrafficMap.png"))

                # display on map page
                if self.rad_map_traffic.get_active():
                    img_map = self.traffic_map.resize((200, 200), Image.LANCZOS)
                    self.img_map.set_from_pixbuf(imgToPixbuf(img_map))

                if self.map_viewer is not None:
                    self.map_viewer.updated()

    def processWeatherOverlay(self, filename, data):
        r = re.compile(r"^DWRO_(.*)_.*_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2}).*$")
        m = r.match(filename)

        if m:
            # get time from map tile and convert to local time
            dt = datetime.datetime(int(m.group(2)), int(m.group(3)), int(m.group(4)),
                                   int(m.group(5)), int(m.group(6)), tzinfo=tz.tzutc())
            t = dt.astimezone(tz.tzlocal())
            ts = dtToTs(dt)
            map_id = self.map_data["weatherID"]

            if m.group(1) != map_id:
                logging.error("Received weather overlay with the wrong ID: %s", m.group(1))
                return

            if self.map_data["weatherTime"] == ts:
                return  # no need to recreate the map if it hasn't changed

            logging.debug("Got weather overlay")

            self.map_data["weatherTime"] = ts
            wxMapPath = os.path.join("map", "WeatherMap_{}_{}.png".format(map_id, ts))

            # create weather map
            try:
                map_path = os.path.join("map", "BaseMap_" + map_id + ".png")
                if not os.path.isfile(map_path):
                    self.makeBaseMap(self.map_data["weatherID"], self.map_data["weatherPos"])

                img_map = Image.open(map_path).convert("RGBA")
                posTS = (img_map.size[0]-235, img_map.size[1]-29)
                imgTS = self.mkTimestamp(t, img_map.size, posTS)
                imgRadar = Image.open(io.BytesIO(data)).convert("RGBA")
                imgRadar = imgRadar.resize(img_map.size, Image.LANCZOS)
                img_map = Image.alpha_composite(img_map, imgRadar)
                img_map = Image.alpha_composite(img_map, imgTS)
                img_map.save(wxMapPath)
                self.map_data["weatherNow"] = wxMapPath

                # display on map page
                if self.rad_map_weather.get_active():
                    img_map = img_map.resize((200, 200), Image.LANCZOS)
                    self.img_map.set_from_pixbuf(imgToPixbuf(img_map))

                self.processWeatherMaps()  # get rid of old maps and add new ones to the list
                if self.map_viewer is not None:
                    self.map_viewer.updated()

            except OSError:
                logging.error("Error creating weather map")
                self.map_data["weatherTime"] = 0

    def processWeatherInfo(self, data):
        weatherID = None
        weatherPos = None

        for line in data.decode().split("\n"):
            if "DWR_Area_ID=" in line:
                r = re.compile("^DWR_Area_ID=\"(.+)\"$")
                m = r.match(line)
                weatherID = m.group(1)

            elif "Coordinates=" in line:
                r = re.compile(r"^Coordinates=.*\((-?\d+\.\d+),(-?\d+\.\d+)\).*\((-?\d+\.\d+),(-?\d+\.\d+)\).*$")
                m = r.match(line)
                weatherPos = [float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))]

        if weatherID is not None and weatherPos is not None:
            if self.map_data["weatherID"] != weatherID or self.map_data["weatherPos"] != weatherPos:
                logging.debug("Got position: (%n, %n) (%n, %n)", *weatherPos)
                self.map_data["weatherID"] = weatherID
                self.map_data["weatherPos"] = weatherPos

                self.makeBaseMap(weatherID, weatherPos)
                self.weather_maps = []
                self.processWeatherMaps()

    def processWeatherMaps(self):
        numberOfMaps = 0
        r = re.compile("^map.WeatherMap_([a-zA-Z0-9]+)_([0-9]+).png")
        now = dtToTs(datetime.datetime.now(tz.tzutc()))
        files = glob.glob(os.path.join("map", "WeatherMap_") + "*.png")
        files.sort()
        for f in files:
            m = r.match(f)
            if m:
                map_id = m.group(1)
                ts = int(m.group(2))

                # remove weather maps older than 12 hours
                if now - ts > 60*60*12:
                    try:
                        if f in self.weather_maps:
                            self.weather_maps.pop(self.weather_maps.index(f))
                        os.remove(f)
                        logging.debug("Deleted old weather map: %s", f)
                    except OSError:
                        logging.error("Failed to delete old weather map: %s", f)

                # skip if not the correct location
                elif map_id == self.map_data["weatherID"]:
                    if f not in self.weather_maps:
                        self.weather_maps.append(f)
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
        map_path = os.path.join("map", "BaseMap_" + map_id + ".png")
        if os.path.isfile(self.MAP_FILE):
            if not os.path.isfile(map_path):
                logging.debug("Creating new map: %s", map_path)
                px = self.getMapArea(*pos)
                map_img = Image.open(self.MAP_FILE).crop(px)
                map_img.save(map_path)
                logging.debug("Finished creating map")
        else:
            logging.error("Map file not found: %s", self.MAP_FILE)
            map_img = Image.new("RGBA", (pos[2]-pos[1], pos[3]-pos[1]), "white")
            map_img.save(map_path)

    def checkTiles(self, t):
        # check if all the tiles have been received
        for i in range(3):
            for j in range(3):
                if self.map_tiles[i][j] != t:
                    return False
        return True

    def mkTimestamp(self, t, size, pos):
        # create a timestamp image to overlay on the weathermap
        x, y = pos
        text = "{:04g}-{:02g}-{:02g} {:02g}:{:02g}".format(t.year, t.month, t.day, t.hour, t.minute)
        imgTS = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(imgTS)
        font = ImageFont.truetype("DejaVuSansMono.ttf", 24)
        draw.rectangle((x, y, x+231, y+25), outline="black", fill=(128, 128, 128, 96))
        draw.text((x+3, y), text, fill="black", font=font)
        return imgTS

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
        if self.stream_info["Bitrate"] == 0:
            self.stream_info["Bitrate"] = kbps
        else:
            self.stream_info["Bitrate"] = 0.99 * self.stream_info["Bitrate"] + 0.01 * kbps

    def update_ber(self, cber):
        ber = self.stream_info["BER"]
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
            self.stream_info["Gain"] = self.radio.get_gain()
            # TODO: update the GUI?
        elif evt_type == nrsc5.EventType.LOST_SYNC:
            pass  # TODO: update the GUI?
        elif evt_type == nrsc5.EventType.MER:
            self.stream_info["MER"] = [evt.lower, evt.upper]
        elif evt_type == nrsc5.EventType.BER:
            self.update_ber(evt.cber)
        elif evt_type == nrsc5.EventType.HDC:
            if evt.program == self.stream_num:
                self.update_bitrate(len(evt.data) * 8)
        elif evt_type == nrsc5.EventType.AUDIO:
            if evt.program == self.stream_num:
                self.audio_queue.put(evt.data)
        elif evt_type == nrsc5.EventType.ID3:
            if evt.program == self.stream_num:
                if evt.title:
                    self.stream_info["Title"] = evt.title
                if evt.artist:
                    self.stream_info["Artist"] = evt.artist
                if evt.album:
                    self.stream_info["Album"] = evt.album
                if evt.xhdr:
                    if evt.xhdr.param != self.last_xhdr:
                        self.last_xhdr = evt.xhdr.param
                        self.xhdr_changed = True
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
                                self.traffic_port = component.data.port
                            elif component.data.mime == nrsc5.MIMEType.TTN_STM_WEATHER:
                                self.weather_port = component.data.port
        elif evt_type == nrsc5.EventType.LOT:
            logging.debug("LOT port=%s", evt.port)

            if self.map_dir is not None:
                if evt.port == self.traffic_port:
                    if evt.name.startswith("TMT_"):
                        self.processTrafficMap(evt.name, evt.data)
                elif evt.port == self.weather_port:
                    if evt.name.startswith("DWRO_"):
                        self.processWeatherOverlay(evt.name, evt.data)
                    elif evt.name.startswith("DWRI_"):
                        self.processWeatherInfo(evt.data)

            if self.aas_dir is not None:
                path = os.path.join(self.aas_dir, evt.name)
                for i, stream in enumerate(self.streams):
                    if evt.port == stream.get("image"):
                        logging.debug("Got album cover: %s", evt.name)
                        with open(path, "wb") as f:
                            f.write(evt.data)
                        if i == self.stream_num:
                            self.stream_info["Cover"] = evt.name
                    elif evt.port == stream.get("logo"):
                        logging.debug("Got station logo: %s", evt.name)
                        with open(path, "wb") as f:
                            f.write(evt.data)
                        self.station_logos[self.station_str][i] = evt.name
                        if i == self.stream_num:
                            self.stream_info["Logo"] = evt.name

        elif evt_type == nrsc5.EventType.SIS:
            if evt.name:
                self.stream_info["Callsign"] = evt.name
            if evt.slogan:
                self.stream_info["Slogan"] = evt.slogan

    def getControls(self):
        # setup gui
        builder = Gtk.Builder()
        builder.add_from_file("mainForm.glade")
        builder.connect_signals(self)

        # Windows
        self.main_window = builder.get_object("main_window")
        self.main_window.connect("delete-event", self.shutdown)
        self.main_window.connect("destroy", Gtk.main_quit)
        self.about_dialog = None

        # get controls
        self.notebook_main = builder.get_object("notebook_main")
        self.img_cover = builder.get_object("img_cover")
        self.img_map = builder.get_object("img_map")
        self.spin_freq = builder.get_object("spin_freq")
        self.spin_stream = builder.get_object("spin_stream")
        self.spin_gain = builder.get_object("spin_gain")
        self.spin_ppm = builder.get_object("spin_ppm")
        self.spin_rtl = builder.get_object("spin_rtl")
        self.cb_auto_gain = builder.get_object("cb_auto_gain")
        self.btn_play = builder.get_object("btn_play")
        self.btn_stop = builder.get_object("btn_stop")
        self.btn_bookmark = builder.get_object("btn_bookmark")
        self.btn_delete = builder.get_object("btn_delete")
        self.rad_map_traffic = builder.get_object("rad_map_traffic")
        self.rad_map_weather = builder.get_object("rad_map_weather")
        self.txt_title = builder.get_object("txt_title")
        self.txt_artist = builder.get_object("txt_artist")
        self.txt_album = builder.get_object("txt_album")
        self.lbl_name = builder.get_object("lbl_name")
        self.lbl_slogan = builder.get_object("lbl_slogan")
        self.lbl_callsign = builder.get_object("lbl_callsign")
        self.lbl_gain = builder.get_object("lbl_gain")
        self.lbl_bitrate = builder.get_object("lbl_bitrate")
        self.lbl_bitrate2 = builder.get_object("lbl_bitrate2")
        self.lbl_error = builder.get_object("lbl_error")
        self.lbl_mer_lower = builder.get_object("lbl_mer_lower")
        self.lbl_mer_upper = builder.get_object("lbl_mer_upper")
        self.lbl_ber_now = builder.get_object("lbl_ber_now")
        self.lbl_ber_avg = builder.get_object("lbl_ber_avg")
        self.lbl_ber_min = builder.get_object("lbl_ber_min")
        self.lbl_ber_max = builder.get_object("lbl_ber_max")
        self.lv_bookmarks = builder.get_object("lv_bookmarks")
        self.lsBookmarks = Gtk.ListStore(str, str, int)

        self.lv_bookmarks.set_model(self.lsBookmarks)
        self.lv_bookmarks.get_selection().connect("changed", self.on_lv_bookmarks_selection_changed)

    def initStreamInfo(self):
        self.stream_info = {
            "Callsign": "",
            "Slogan": "",
            "Title": "",
            "Album": "",
            "Artist": "",
            "Cover": "",
            "Logo": "",
            "Bitrate": 0,
            "MER": [0, 0],
            "BER": [0, 0, 0, 0],
            "Gain": 0
        }

        self.streams = [{}, {}, {}, {}]
        self.traffic_port = -1
        self.weather_port = -1
        self.lastType = 0

        # clear status info
        self.lbl_callsign.set_label("")
        self.lbl_bitrate.set_label("")
        self.lbl_bitrate2.set_label("")
        self.lbl_error.set_label("")
        self.lbl_gain.set_label("")
        self.txt_title.set_text("")
        self.txt_artist.set_text("")
        self.txt_album.set_text("")
        self.img_cover.clear()
        self.lbl_name.set_label("")
        self.lbl_slogan.set_label("")
        self.lbl_slogan.set_tooltip_text("")
        self.lbl_mer_lower.set_label("")
        self.lbl_mer_upper.set_label("")
        self.lbl_ber_now.set_label("")
        self.lbl_ber_avg.set_label("")
        self.lbl_ber_min.set_label("")
        self.lbl_ber_max.set_label("")

    def loadSettings(self):
        try:
            with open("station_logos.json", mode="r") as f:
                self.station_logos = json.load(f)
        except (OSError, json.decoder.JSONDecodeError):
            logging.warning("Unable to load station logo database")

        # load settings
        try:
            with open("config.json", mode="r") as f:
                config = json.load(f)

                if "MapData" in config:
                    self.map_data = config["MapData"]
                    if self.map_data["mapMode"] == 0:
                        self.rad_map_traffic.set_active(True)
                        self.rad_map_traffic.toggled()
                    elif self.map_data["mapMode"] == 1:
                        self.rad_map_weather.set_active(True)
                        self.rad_map_weather.toggled()

                if "Width" and "Height" in config:
                    self.main_window.resize(config["Width"], config["Height"])

                self.main_window.move(config["WindowX"], config["WindowY"])
                self.spin_freq.set_value(config["Frequency"])
                self.spin_stream.set_value(config["Stream"])
                self.spin_gain.set_value(config["Gain"])
                self.cb_auto_gain.set_active(config["AutoGain"])
                self.spin_ppm.set_value(config["PPMError"])
                self.spin_rtl.set_value(config["RTL"])
                self.bookmarks = config["Bookmarks"]
                for bookmark in self.bookmarks:
                    self.lsBookmarks.append(bookmark)
        except (OSError, json.decoder.JSONDecodeError, KeyError):
            logging.warning("Unable to load config")

        # create aas directory
        self.aas_dir = os.path.join(sys.path[0], "aas")
        if not os.path.isdir(self.aas_dir):
            try:
                os.mkdir(self.aas_dir)
            except OSError:
                logging.error("Unable to create AAS directory")
                self.aas_dir = None

        # create map directory
        self.map_dir = os.path.join(sys.path[0], "map")
        if not os.path.isdir(self.map_dir):
            try:
                os.mkdir(self.map_dir)
            except OSError:
                logging.error("Unable to create map directory")
                self.map_dir = None

    def shutdown(self, *_args):
        # stop map viewer animation if it's running
        if self.map_viewer is not None and self.map_viewer.animateTimer is not None:
            self.map_viewer.animateTimer.cancel()
            self.map_viewer.animateStop = True

            while self.map_viewer.animateBusy:
                logging.debug("Animation busy - stopping")
                if self.map_viewer.animateTimer is not None:
                    self.map_viewer.animateTimer.cancel()
                time.sleep(0.25)

        self.playing = False

        # kill nrsc5 if it's running
        if self.radio:
            self.radio.stop()
            self.radio.close()
            self.radio = None

        # shut down status timer if it's running
        if self.status_timer is not None:
            self.status_timer.cancel()

        self.audio_queue.put(None)
        self.audio_thread.join()

        # save settings
        try:
            with open("config.json", mode="w") as f:
                winX, winY = self.main_window.get_position()
                width, height = self.main_window.get_size()
                config = {
                    "CfgVersion": "1.2.0",
                    "WindowX": winX,
                    "WindowY": winY,
                    "Width": width,
                    "Height": height,
                    "Frequency": self.spin_freq.get_value(),
                    "Stream": int(self.spin_stream.get_value()),
                    "Gain": self.spin_gain.get_value(),
                    "AutoGain": self.cb_auto_gain.get_active(),
                    "PPMError": int(self.spin_ppm.get_value()),
                    "RTL": int(self.spin_rtl.get_value()),
                    "Bookmarks": self.bookmarks,
                    "MapData": self.map_data,
                }
                # sort bookmarks
                config["Bookmarks"].sort(key=lambda t: t[2])

                json.dump(config, f, indent=2)

            with open("station_logos.json", mode="w") as f:
                json.dump(self.station_logos, f, indent=2)
        except OSError:
            logging.error("Unable to save config")


class NRSC5_Map(object):
    def __init__(self, parent, callback, data):
        # setup gui
        builder = Gtk.Builder()
        builder.add_from_file("mapForm.glade")
        builder.connect_signals(self)

        self.parent = parent
        self.callback = callback
        self.data = data  # map data
        self.animateTimer = None
        self.animateBusy = False
        self.animateStop = False
        self.weather_maps = parent.weather_maps  # list of weather maps sorted by time
        self.mapIndex = 0  # the index of the next weather map to display

        # get the controls
        self.map_window = builder.get_object("map_window")
        self.img_map = builder.get_object("img_map")
        self.rad_map_weather = builder.get_object("rad_map_weather")
        self.rad_map_traffic = builder.get_object("rad_map_traffic")
        self.chk_animate = builder.get_object("chk_animate")
        self.chk_scale = builder.get_object("chk_scale")
        self.spin_speed = builder.get_object("spin_speed")
        self.adjSpeed = builder.get_object("adjSpeed")
        self.imgKey = builder.get_object("imgKey")

        self.map_window.connect("delete-event", self.on_map_window_delete)

        self.config = data["viewerConfig"]
        self.map_window.resize(*self.config["windowSize"])
        self.map_window.move(*self.config["windowPos"])
        if self.config["mode"] == 0:
            self.rad_map_traffic.set_active(True)
        elif self.config["mode"] == 1:
            self.rad_map_weather.set_active(True)
        self.setMap(self.config["mode"])

        self.chk_animate.set_active(self.config["animate"])
        self.chk_scale.set_active(self.config["scale"])
        self.spin_speed.set_value(self.config["animationSpeed"])

    def on_rad_map_toggled(self, btn):
        if btn.get_active():
            if btn == self.rad_map_traffic:
                self.config["mode"] = 0
                self.imgKey.set_visible(False)

                # stop animation if it's enabled
                if self.animateTimer is not None:
                    self.animateTimer.cancel()
                    self.animateTimer = None

                self.setMap(0)  # show the traffic map

            elif btn == self.rad_map_weather:
                self.config["mode"] = 1
                self.imgKey.set_visible(True)  # show the key for the weather radar

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
            self.animateTimer = threading.Timer(self.config["animationSpeed"], self.animate)
            self.animateTimer.start()
        else:
            # stop animation
            if self.animateTimer is not None:
                self.animateTimer.cancel()
                self.animateTimer = None
            self.mapIndex = len(self.weather_maps)-1  # reset the animation index
            self.setMap(self.config["mode"])  # show the most recent map

    def on_chk_scale_toggled(self, btn):
        self.config["scale"] = btn.get_active()
        if self.config["mode"] == 1:
            if self.config["animate"]:
                i = len(self.weather_maps)-1 if (self.mapIndex-1 < 0) else self.mapIndex-1
                self.showImage(self.weather_maps[i], self.config["scale"])
            else:
                self.showImage(self.data["weatherNow"], self.config["scale"])

    def on_spin_speed_value_changed(self, _spn):
        self.config["animationSpeed"] = self.adjSpeed.get_value()

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

        self.config["windowPos"] = self.map_window.get_position()
        self.config["windowSize"] = self.map_window.get_size()
        self.callback()

    def animate(self):
        filename = self.weather_maps[self.mapIndex] if self.weather_maps else ""
        if os.path.isfile(filename):
            self.animateBusy = True

            if self.config["scale"]:
                map_img = imgToPixbuf(Image.open(filename).resize((600, 600), Image.LANCZOS))
            else:
                map_img = imgToPixbuf(Image.open(filename))

            if self.config["animate"] and self.config["mode"] == 1 and not self.animateStop:
                self.img_map.set_from_pixbuf(map_img)
                self.mapIndex += 1
                if self.mapIndex >= len(self.weather_maps):
                    self.mapIndex = 0
                    self.animateTimer = threading.Timer(2, self.animate)  # show the last image for a longer time
                else:
                    self.animateTimer = threading.Timer(self.config["animationSpeed"], self.animate)

                self.animateTimer.start()
            else:
                self.animateTimer = None

            self.animateBusy = False
        else:
            self.chk_animate.set_active(False)  # stop animation if image was not found
            self.mapIndex = 0

    def showImage(self, filename, scale):
        if os.path.isfile(filename):
            if scale:
                map_img = Image.open(filename).resize((600, 600), Image.LANCZOS)
            else:
                map_img = Image.open(filename)

            self.img_map.set_from_pixbuf(imgToPixbuf(map_img))
        else:
            self.img_map.set_from_stock(Gtk.STOCK_MISSING_IMAGE, Gtk.IconSize.LARGE_TOOLBAR)

    def setMap(self, map_type):
        if map_type == 0:
            self.showImage(os.path.join("map", "TrafficMap.png"), False)
        elif map_type == 1:
            self.showImage(self.data["weatherNow"], self.config["scale"])

    def updated(self):
        if self.config["mode"] == 0:
            self.setMap(0)
        elif self.config["mode"] == 1:
            self.setMap(1)
            self.mapIndex = len(self.weather_maps)-1


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
    nrsc5_gui.main_window.show()
    Gtk.main()
