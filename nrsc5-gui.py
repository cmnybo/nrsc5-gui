#!/usr/bin/python
# -*- coding: utf-8 -*-

#    NRSC5 GUI - A graphical interface for nrsc5
#    Copyright (C) 2017  Cody Nybo
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
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os, sys, shutil, re, tempfile, md5, gtk, gobject, json
from subprocess import Popen, PIPE
from threading import Timer, Thread

# if nrsc5 and mpv are not in the system path, set the full path here
nrsc5Path = "nrsc5"
mpvPath = "mpv"

# print debug messages to stdout
debugMessages = False

class NRSC5_GUI(object):
    def __init__(self):
        gobject.threads_init()
        
        self.getControls()          # get controls and windows
        self.initStreamInfo()       # initilize stream info and clear status widgets
        
        self.nrsc5        = None    # nrsc5 process
        self.mpv          = None    # mpv process
        self.playerThread = None    # player thread
        self.playing      = False   # currently playing
        self.statusTimer  = None    # status update timer
        self.imageChanged = False   # has the album art changed
        self.xhdrChanged  = False   # has the HDDR data changed
        self.nrsc5Args    = []      # arguments for nrsc5
        self.logFile      = None    # nrsc5 log file
        self.lastImage    = ""      # last image file displayed
        self.lastXHDR     = ""      # the last XHDR data received
        self.stationStr   = ""      # current station frequency (string)
        self.streamNum    = 0       # current station stream number
        self.bookmarks    = []      # station bookmarks
        self.stationLogos = {}      # station logos
        self.bookmarked   = False   # is current station bookmarked
        
        # setup bookmarks listview
        nameRenderer = gtk.CellRendererText()
        nameRenderer.set_property("editable", True)
        nameRenderer.connect("edited", self.on_bookmarkNameEdited)
        
        colStation = gtk.TreeViewColumn("Station", gtk.CellRendererText(), text=0)
        colName    = gtk.TreeViewColumn("Name", nameRenderer, text=1)
        
        colStation.set_resizable(True)
        colStation.set_sort_column_id(2)
        colName.set_resizable(True)
        colName.set_sort_column_id(1)
        
        self.lvBookmarks.append_column(colStation)
        self.lvBookmarks.append_column(colName)
        
        # regex for getting nrsc5 output
        self.regex = [
            re.compile("^.*pids\.c:[\d]+: Station Name: (.*)$"),                                                    #  0 match station name
            re.compile("^.*pids\.c:[\d]+: Station location: (-?[\d]+\.[\d]+) (-?[\d]+\.[\d]+), ([\d]+)m$"),         #  1 match station location
            re.compile("^.*pids\.c:[\d]+: Slogan: (.*)$"),                                                          #  2 match station slogan
            re.compile("^.*output\.c:[\d]+: Audio bit rate: (.*) kbps$"),                                           #  3 match audio bit rate
            re.compile("^.*output\.c:[\d]+: Title: (.*)$"),                                                         #  4 match title
            re.compile("^.*output\.c:[\d]+: Artist: (.*)$"),                                                        #  5 match artist
            re.compile("^.*output\.c:[\d]+: Album: (.*)$"),                                                         #  6 match album
            re.compile("^.*output\.c:[\d]+: File (.*\.(?:jpg|png)), size ([\d]+), port ([0-9a-fA-F]+).*$"),         #  7 match album art
            re.compile("^.*sync\.c:[\d]+: MER: (-?[\d]+\.[\d]+) dB \(lower\), (-?[\d]+\.[\d]+) dB \(upper\)$"),     #  8 match MER
            re.compile("^.*decode\.c:[\d]+: BER: (0\.[\d]+), avg: (0\.[\d]+), min: (0\.[\d]+), max: (0\.[\d]+)$"),  #  9 match BER
            re.compile("^.*main\.c:[\d]+: Best gain: (.*)$"),                                                       # 10 match gain
            re.compile("^.*output\.c:[\d]+: ([0-9a-fA-F]{2}) ([0-9a-fA-F]{2}) ([0-9a-fA-F]{2}) ([0-9a-fA-F]{2})$"), # 11 match stream
            re.compile("^.*output\.c:[\d]+: Port ([0-9a-fA-F]+), type ([\d]+), size ([\d]+)$"),                     # 12 match port
            re.compile("^.*output\.c:[\d]+: XHDR tag: ((?:[0-9A-Fa-f]{2} ?)+).*$"),                                 # 13 match xhdr tag
            re.compile("^.*output\.c:[\d]+: Unique file identifier: PPC;07; ([\S]+).*$")                            # 14 match unique file id
        ]
        
        self.loadSettings()
    
    def on_btnPlay_clicked(self, btn):
        # start playback
        if (not self.playing):
            
            self.nrsc5Args = [nrsc5Path, "-o", "-", "-f", "adts"]
            
            # enable aas output if temp dir was created
            if (self.aasDir is not None):
                self.nrsc5Args.append("--dump-aas-files")
                self.nrsc5Args.append(self.aasDir)
            
            # set gain if auto gain is not selected
            if (not self.cbAutoGain.get_active()):
                self.streamInfo["Gain"] = self.spinGain.get_value()
                self.nrsc5Args.append("-g")
                self.nrsc5Args.append(str(int(self.streamInfo["Gain"]*10)))
            
            # set ppm error if not zero
            if (self.spinPPM.get_value() != 0):
                self.nrsc5Args.append("-p")
                self.nrsc5Args.append(str(int(self.spinPPM.get_value())))
            
            # set rtl device number if not zero
            if (self.spinRTL.get_value() != 0):
                self.nrsc5Args.append("-d")
                self.nrsc5Args.append(str(int(self.spinRTL.get_value())))
            
            # set frequency and stream
            self.nrsc5Args.append(str(self.spinFreq.get_value()))
            self.nrsc5Args.append(str(int(self.spinStream.get_value()-1)))
                        
            # start the timer
            self.statusTimer = Timer(1, self.checkStatus)
            self.statusTimer.start()
            
            # disable the controls
            self.spinFreq.set_sensitive(False)
            self.spinStream.set_sensitive(False)
            self.spinGain.set_sensitive(False)
            self.spinPPM.set_sensitive(False)
            self.spinRTL.set_sensitive(False)
            self.btnPlay.set_sensitive(False)
            self.btnStop.set_sensitive(True)
            self.cbAutoGain.set_sensitive(False)
            self.playing = True
            self.lastXHDR = ""
            
            # start the player thread
            self.playerThread = Thread(target=self.play)
            self.playerThread.start()
            
            self.stationStr = str(self.spinFreq.get_value())
            self.stationNum = int(self.spinStream.get_value())-1
            
            if (self.stationLogos.has_key(self.stationStr)):
                # show station logo if it's cached
                logo = os.path.join(self.aasDir, self.stationLogos[self.stationStr][self.stationNum])
                if (os.path.isfile(logo)):
                    self.streamInfo["Logo"] = self.stationLogos[self.stationStr][self.stationNum]
                    pixbuf = gtk.gdk.pixbuf_new_from_file(logo)
                    pixbuf = pixbuf.scale_simple(200, 200, gtk.gdk.INTERP_HYPER)
                    self.imgCover.set_from_pixbuf(pixbuf)
            else:
                # add entry in database for the station if it doesn't exist
                self.stationLogos[self.stationStr] = ["", "", "", ""]
            
            # check if station is bookmarked
            self.bookmarked = False
            freq = int((self.spinFreq.get_value()+0.005)*100) + int(self.spinStream.get_value())
            for b in self.bookmarks:
                if (b[2] == freq):
                    self.bookmarked = True
                    break
            
            self.btnBookmark.set_sensitive(not self.bookmarked)
            if (self.notebookMain.get_current_page() != 3):
                self.btnDelete.set_sensitive(self.bookmarked)
    
    def on_btnStop_clicked(self, btn):
        # stop playback
        if (self.playing):
            self.playing = False
            
            # shutdown nrsc5 
            if (self.nrsc5 is not None and not self.nrsc5.poll()):
                self.nrsc5.terminate()
            
            # shutdown mpv
            if (self.mpv is not None and not self.mpv.poll()):
                self.mpv.terminate()
            
            if (self.playerThread is not None):
                self.playerThread.join(1)
            
            # stop timer
            self.statusTimer.cancel()
            self.statusTimer = None
            
            # enable controls
            if (not self.cbAutoGain.get_active()):
                self.spinGain.set_sensitive(True)
            self.spinFreq.set_sensitive(True)
            self.spinStream.set_sensitive(True)
            self.spinPPM.set_sensitive(True)
            self.spinRTL.set_sensitive(True)
            self.btnPlay.set_sensitive(True)
            self.btnStop.set_sensitive(False)
            self.btnBookmark.set_sensitive(False)
            self.cbAutoGain.set_sensitive(True)
            
            # clear stream info
            self.initStreamInfo()
            
            self.btnBookmark.set_sensitive(False)
            if (self.notebookMain.get_current_page() != 3):
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
        
        if (self.notebookMain.get_current_page() != 3):
            self.btnDelete.set_sensitive(True)  # enable delete button

    def on_btnDelete_clicked(self, btn):
        # select current station if not on bookmarks page
        if (self.notebookMain.get_current_page() != 3):
            station = int((self.spinFreq.get_value()+0.005)*100) + int(self.spinStream.get_value())
            for i in range(0, len(self.lsBookmarks)):
                if (self.lsBookmarks[i][2] == station):            
                    self.lvBookmarks.set_cursor(i)
                    break
        
        # get station of selected row
        (model, iter) = self.lvBookmarks.get_selection().get_selected()
        station = model.get_value(iter, 2)
        
        # remove row
        model.remove(iter)
        
        # remove bookmark
        for i in range(0, len(self.bookmarks)):
            if (self.bookmarks[i][2] == station):
                self.bookmarks.pop(i)
                break
        
        if (self.notebookMain.get_current_page() != 3 and self.playing):
            self.btnBookmark.set_sensitive(True)
            self.bookmarked = False

    def on_btnAbout_activate(self, btn):
        # sets up and displays about dialog
        if self.about_dialog:
            self.about_dialog.present()
            return

        authors = [
        "Cody Nybo <cmnybo@gmail.com>"
        ]

        license = """
        NRSC5 GUI - A graphical interface for nrsc5
        Copyright (C) 2017  Cody Nybo

        This program is free software: you can redistribute it and/or modify
        it under the terms of the GNU General Public License as published by
        the Free Software Foundation, either version 3 of the License, or
        (at your option) any later version.

        This program is distributed in the hope that it will be useful,
        but WITHOUT ANY WARRANTY; without even the implied warranty of
        MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
        GNU General Public License for more details.

        You should have received a copy of the GNU General Public License
        along with this program.  If not, see <http://www.gnu.org/licenses/>."""

        about_dialog = gtk.AboutDialog()
        about_dialog.set_transient_for(self.mainWindow)
        about_dialog.set_destroy_with_parent(True)
        about_dialog.set_name("NRSC5 GUI")
        about_dialog.set_version("1.0.0")
        about_dialog.set_copyright("Copyright \xc2\xa9 2017 Cody Nybo")
        about_dialog.set_website("https://github.com/cmnybo")
        about_dialog.set_comments("A graphical interface for nrsc5.")
        about_dialog.set_authors(authors)
        about_dialog.set_license(license)
        about_dialog.set_logo(gtk.gdk.pixbuf_new_from_file("logo.png"))

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

    def on_cbAutoGain_toggled(self, btn):
        self.spinGain.set_sensitive(not btn.get_active())

    def on_listviewBookmarks_row_activated(self, treeview, path, view_column):
        if (len(path) != 0):
            # get station from bookmark row
            tree_iter = treeview.get_model().get_iter(path[0])
            station   = treeview.get_model().get_value(tree_iter, 2)
            
            # set frequency and stream
            self.spinFreq.set_value(float(int(station/10)/10.0))
            self.spinStream.set_value(station%10)
            
            # stop playback if playing
            if (self.playing): self.on_btnStop_clicked(None)
            
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
            if (b[2] == self.lsBookmarks[path][2]):
                b[1] = text
                break

    def on_notebookMain_switch_page(self, notebook, page, page_num):
        # disable delete button if not on bookmarks page and station is not bookmarked
        if (page_num != 3 and (not self.bookmarked or not self.playing)):
            self.btnDelete.set_sensitive(False)
        # enable delete button if not on bookmarks page and station is bookmarked
        elif (page_num != 3 and self.bookmarked):
            self.btnDelete.set_sensitive(True)
        # enable delete button if on bookmarks page and a bookmark is selected
        else:
            (model, iter) = self.lvBookmarks.get_selection().get_selected()
            self.btnDelete.set_sensitive(iter is not None)

    def play(self):
        FNULL = open(os.devnull, 'w')
        
        # run nrsc5 and output stdout & stderr to pipes
        self.nrsc5 = Popen(self.nrsc5Args, stderr=PIPE, stdout=PIPE, universal_newlines=True)
        
        # run mpv and read from stdin & output to /dev/null
        self.mpv = Popen([mpvPath, "-"], stdin=self.nrsc5.stdout, stderr=FNULL, stdout=FNULL)
        
        while True:
            # read output from nrsc5
            output = self.nrsc5.stderr.readline()
            # parse the output
            self.parseFeedback(output)
            
            # write output to log file if enabled
            if (self.cbLog.get_active() and self.logFile is not None):
                self.logFile.write(output)
                self.logFile.flush()
            
            # check if nrsc5 or mpv has exited
            if (self.nrsc5.poll() and not self.playing):
                # cleanup if shutdown
                if (debugMessages): print ("Process Terminated")
                self.mpv = None
                self.nrsc5 = None
                break
            elif (self.nrsc5.poll() and self.playing):
                # restart nrsc5 and mpv if nrsc5 crashes
                if (debugMessages): print ("Restarting NRSC5")
                self.nrsc5 = Popen(self.nrsc5Args, stderr=PIPE, stdout=PIPE, universal_newlines=True)
                self.mpv.kill()
                self.mpv = Popen([mpvPath, "-"], stdin=self.nrsc5.stdout, stderr=FNULL, stdout=FNULL)
            elif (self.mpv.poll() and self.playing):
                # restart mpv if it crashes
                if (debugMessages): print ("Restarting MPV")
                self.mpv = Popen([mpvPath, "-"], stdin=self.nrsc5.stdout, stderr=FNULL, stdout=FNULL)
    
    def checkStatus(self):
        # update status information
        def update():
            gtk.threads_enter()
            try:
                imagePath = ""
                image = ""
                ber = [self.streamInfo["BER"][0]*100,self.streamInfo["BER"][1]*100,self.streamInfo["BER"][2]*100,self.streamInfo["BER"][3]*100]
                self.txtTitle.set_text(self.streamInfo["Title"])
                self.txtArtist.set_text(self.streamInfo["Artist"])
                self.txtAlbum.set_text(self.streamInfo["Album"])
                self.lblBitRate.set_label("{:3.1f} kbps".format(self.streamInfo["Bitrate"]))
                self.lblBitRate2.set_label("{:3.1f} kbps".format(self.streamInfo["Bitrate"]))
                self.lblError.set_label("{:2.3f}% Error".format(self.streamInfo["BER"][1]*100))
                self.lblCall.set_label(self.streamInfo["Callsign"])
                self.lblName.set_label(self.streamInfo["Callsign"])
                self.lblSlogan.set_label(self.streamInfo["Slogan"])
                self.lblSlogan.set_tooltip_text(self.streamInfo["Slogan"])
                self.lblMerLower.set_label("{:1.2f} dB".format(self.streamInfo["MER"][0]))
                self.lblMerUpper.set_label("{:1.2f} dB".format(self.streamInfo["MER"][1]))
                self.lblBerNow.set_label("{:1.2f}% (Now)".format(ber[0]))
                self.lblBerAvg.set_label("{:1.2f}% (Avg)".format(ber[1]))
                self.lblBerMin.set_label("{:1.2f}% (Min)".format(ber[2]))
                self.lblBerMax.set_label("{:1.2f}% (Max)".format(ber[3]))
                
                if (self.cbAutoGain.get_active()):
                    self.spinGain.set_value(self.streamInfo["Gain"])
                
                # from what I can tell, album art is displayed if the XHDR packet is 8 bytes long
                # and the station logo is displayed if it's 6 bytes long
                if (len(self.lastXHDR.split(' ')) == 8):
                    imagePath = os.path.join(self.aasDir, self.streamInfo["Cover"])
                    image = self.streamInfo["Cover"]
                elif (len(self.lastXHDR.split(' ')) == 6):
                    imagePath = os.path.join(self.aasDir, self.streamInfo["Logo"])
                    image = self.streamInfo["Logo"]
                    if (not os.path.isfile(imagePath)):
                        self.imgCover.clear()
                    
                # resize and display image if it changed and exists
                if (self.xhdrChanged and self.lastImage != image and os.path.isfile(imagePath)):
                    self.xhdrChanged = False
                    self.lastImage = image
                    pixbuf = gtk.gdk.pixbuf_new_from_file(imagePath)
                    pixbuf = pixbuf.scale_simple(200, 200, gtk.gdk.INTERP_HYPER)
                    self.imgCover.set_from_pixbuf(pixbuf)
                    if (debugMessages): print ("Image Changed")
            finally:
                gtk.threads_leave()        
        
        if (self.playing):
            gobject.idle_add(update)
            self.statusTimer = Timer(1, self.checkStatus)
            self.statusTimer.start()

    def parseFeedback(self, line):
        if (self.regex[4].match(line)):
            # match title
            m = self.regex[4].match(line)
            self.streamInfo["Title"] = m.group(1)
        elif (self.regex[5].match(line)):
            # match artist
            m = self.regex[5].match(line)
            self.streamInfo["Artist"] = m.group(1)
        elif (self.regex[6].match(line)):
            # match album
            m = self.regex[6].match(line)
            self.streamInfo["Album"] = m.group(1)
        elif (self.regex[3].match(line)):
            # match audio bit rate
            m = self.regex[3].match(line)
            self.streamInfo["Bitrate"] = float(m.group(1))
        elif (self.regex[8].match(line)):
            # match MER
            m = self.regex[8].match(line)
            self.streamInfo["MER"] = [float(m.group(1)), float(m.group(2))]
        elif (self.regex[9].match(line)):
            # match BER
            m = self.regex[9].match(line)
            self.streamInfo["BER"] = [float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))]
        elif (self.regex[13].match(line)):
            # match xhdr
            m = self.regex[13].match(line)
            xhdr = m.group(1)
            if (xhdr != self.lastXHDR):
                self.lastXHDR = xhdr
                self.xhdrChanged = True
                if (debugMessages): print ("XHDR Changed: {:s}".format(xhdr))
        elif (self.regex[7].match(line)):
            # match album art
            m = self.regex[7].match(line)
            p = int(m.group(3), 16)
            
            try:
                if (p == self.streams[int(self.spinStream.get_value()-1)][0]):                    
                    self.streamInfo["Cover"] = m.group(1)
                    if (debugMessages): print ("Got Album Cover: " + m.group(1))
                elif (p == self.streams[int(self.spinStream.get_value()-1)][1]):
                    self.streamInfo["Logo"] = m.group(1)
                    self.stationLogos[self.stationStr][self.stationNum] = m.group(1)    # add station logo to database
                    if (debugMessages): print ("Got Station Logo: " + m.group(1))
            except:
                pass
        elif (self.regex[0].match(line)):
            # match station name
            m = self.regex[0].match(line)
            self.streamInfo["Callsign"] = m.group(1)
        elif (self.regex[2].match(line)):
            # match station slogan
            m = self.regex[2].match(line)
            self.streamInfo["Slogan"] = m.group(1)
        elif (self.regex[10].match(line)):
            # match gain
            m = self.regex[10].match(line)
            self.streamInfo["Gain"] = float(m.group(1))/10
        elif (self.regex[11].match(line)):
            # match stream
            m = self.regex[11].match(line)
            t = int(m.group(1), 16) # stream type
            s = int(m.group(2), 16) # stream number
            
            if (debugMessages): print ("Found Stream: Type {:02X}, Number {:02X}". format(t, s))
            self.lastType = t
            if (t == 0x40 and s >= 1 and s <= 4):
                self.numStreams = s
        elif (self.regex[12].match(line)):
            # match port
            m = self.regex[12].match(line)
            p = int(m.group(1), 16)
            if (debugMessages): print ("\tFound Port: {:03X}". format(p))
            
            if (self.lastType == 0x40 and self.numStreams > 0):
                self.streams[self.numStreams-1].append(p)

    def getControls(self):
        # setup gui
        builder = gtk.Builder()
        builder.add_from_file("mainForm.glade")
        builder.connect_signals(self)
        
        # Windows
        self.mainWindow = builder.get_object("mainWindow")
        self.mainWindow.connect("delete-event", self.shutdown)
        self.mainWindow.connect("destroy", gtk.main_quit)
        self.about_dialog = None
        
        # get controls
        self.notebookMain = builder.get_object("notebookMain")
        self.imgCover     = builder.get_object("imgCover")
        self.spinFreq     = builder.get_object("spinFreq")
        self.spinStream   = builder.get_object("spinStream")
        self.spinGain     = builder.get_object("spinGain")
        self.spinPPM      = builder.get_object("spinPPM")
        self.spinRTL      = builder.get_object("spinRTL")
        self.cbAutoGain   = builder.get_object("cbAutoGain")
        self.cbLog        = builder.get_object("cbLog")
        self.btnPlay      = builder.get_object("btnPlay")
        self.btnStop      = builder.get_object("btnStop")
        self.btnBookmark  = builder.get_object("btnBookmark")
        self.btnDelete    = builder.get_object("btnDelete")
        self.txtTitle     = builder.get_object("txtTitle")
        self.txtArtist    = builder.get_object("txtArtist")
        self.txtAlbum     = builder.get_object("txtAlbum")
        self.lblName      = builder.get_object("lblName")
        self.lblSlogan    = builder.get_object("lblSlogan")
        self.lblCall      = builder.get_object("lblCall")
        self.lblBitRate   = builder.get_object("lblBitRate")
        self.lblBitRate2  = builder.get_object("lblBitRate2")
        self.lblError     = builder.get_object("lblError")
        self.lblMerLower  = builder.get_object("lblMerLower")
        self.lblMerUpper  = builder.get_object("lblMerUpper")
        self.lblBerNow    = builder.get_object("lblBerNow")
        self.lblBerAvg    = builder.get_object("lblBerAvg")
        self.lblBerMin    = builder.get_object("lblBerMin")
        self.lblBerMax    = builder.get_object("lblBerMax")
        self.lvBookmarks  = builder.get_object("listviewBookmarks")
        self.lsBookmarks  = gtk.ListStore(str, str, int)
        
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
            "MER": [0,0],           # modulation error ratio: lower, upper
            "BER": [0,0,0,0],       # bit error rate: current, average, min, max
            "Gain": 0               # automatic gain
        }
        
        self.streams      = [[],[],[],[]]
        self.numStreams   = 0
        self.lastType     = 0
        
        # clear status info
        self.lblCall.set_label("")
        self.lblBitRate.set_label("")
        self.lblBitRate2.set_label("")
        self.lblError.set_label("")
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
            with open("stationLogos.json", mode='r') as f:
                self.stationLogos = json.load(f)
        except:
            print ("Error: Unable to load station logo database")
       
        # load settings
        try:
            with open("config.json", mode='r') as f:
                config = json.load(f)
                
                if "Width" in config:
                    self.mainWindow.resize(config["Width"],config["Height"])
                
                self.mainWindow.move(config["WindowX"], config["WindowY"])
                self.spinFreq.set_value(config["Frequency"])
                self.spinStream.set_value(config["Stream"])
                self.spinGain.set_value(config["Gain"])
                self.cbAutoGain.set_active(config["AutoGain"])
                self.spinPPM.set_value(config["PPMError"])
                self.spinRTL.set_value(config["RTL"])
                self.cbLog.set_active(config["LogToFile"])
                self.bookmarks = config["Bookmarks"]
                for bookmark in self.bookmarks:
                    self.lsBookmarks.append(bookmark)
        except:
            print ("Error: Unable to load config")
        
        # create aas directory
        self.aasDir = os.path.join(sys.path[0], "aas")
        if (not os.path.isdir(self.aasDir)):
            try:
                os.mkdir(self.aasDir)
            except:
                print ("Error: Unable to create AAS directory:")
                self.aasDir = None
        
        # open log file
        try:
            self.logFile = open("nrsc5.log", mode='a')
        except:
            print ("Error: Unable to create log file") 
    
    def shutdown(self, *args):
        self.playing = False
                
        # kill nrsc5 if it's running
        if (self.nrsc5 is not None and not self.nrsc5.poll()):
            self.nrsc5.kill()
        
        # kill mpv if it's running
        if (self.mpv is not None and not self.mpv.poll()):
            self.mpv.kill()
        
        # shut down status timer if it's running    
        if (self.statusTimer is not None):
            self.statusTimer.cancel()
        
        # wair for player thread to exit
        if (self.playerThread is not None and self.playerThread.isAlive()):
            self.playerThread.join(1)
        
        # close log file if it's enabled
        if (self.logFile is not None):
            self.logFile.close()
        
        # save settings
        try:
            with open("config.json", mode='w') as f:
                winX, winY = self.mainWindow.get_position()
                width, height = self.mainWindow.get_size()
                config = {
                    "WindowX": winX,
                    "WindowY": winY,
                    "Width":width,
                    "Height":height,
                    "Frequency": self.spinFreq.get_value(),
                    "Stream": int(self.spinStream.get_value()),
                    "Gain": self.spinGain.get_value(),
                    "AutoGain": self.cbAutoGain.get_active(),
                    "PPMError": int(self.spinPPM.get_value()),
                    "RTL": int(self.spinRTL.get_value()),
                    "LogToFile": self.cbLog.get_active(),
                    "Bookmarks": self.bookmarks
                }
                # sort bookmarks
                config["Bookmarks"].sort(key=lambda t: t[2])
                
                json.dump(config, f, indent=2)
            
            with open("stationLogos.json", mode='w') as f:
                json.dump(self.stationLogos, f, indent=2)
        except:
            print ("Error: Unable to save config")

if __name__ == "__main__":
    # show main window and start main thread
    os.chdir(sys.path[0])
    nrsc5_gui = NRSC5_GUI()
    nrsc5_gui.mainWindow.show()
    gtk.main()
