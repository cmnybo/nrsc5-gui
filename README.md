NRSC5-GUI is a graphical interface for [nrsc5](https://github.com/theori-io/nrsc5).  
It makes it easy to play your favorite FM HD radio stations using and RTL-SDR dongle.  
It will also display weather radar and traffic maps if the radio station provides them.

# Dependencies

The folowing programs are required to run NRSC5-GUI

* [Python 2.7.x](https://www.python.org/downloads/release)
* [PyGTK](http://www.pygtk.org/downloads.html)
* [Python Imaging Library](http://pythonware.com/products/pil)
* [NumPy](http://www.numpy.org)
* [Python Dateutil](https://pypi.org/project/python-dateutil)
* [nrsc5](https://github.com/theori-io/nrsc5)
* [mpv](https://mpv.io/installation)


# Setup
1. Install the latest version of Python 2.7, PyGTK, Python Imaging Library, and NumPy.
2. Compile and install nrsc5.
3. Install mpv.
4. Install nrsc5-gui files in a directory where you have write permissions.

The configuration files will be created in the same directory as nrsc5-gui.py.
an aas directory will be created for downloaded files and a map directory will be created to
store weather & traffic maps in.

nrsc5 and mpv should be installed in a directory that is in your `$PATH` environmental variable.  
Otherwise you can edit lines 27 & 28 of nrsc5-gui.py to provide a full path to nrsc5 and mpv.  

# Usage
Open the Settings tab and enter the frequency in MHz of the station you want to play.  
Select the stream (1 is the main stream, some stations have additional streams).  
Set the gain to Auto (you can specify the RF gain in dB in case auto doesn't work for your station).  
You can enter a PPM correction value if your RTL-SDR dongle has an offset.  
If you have more than one RTL-SDR dongle, you can enter the device number for the one you want to use.  
Log to file can be enabled to write the debug information from nrsc5 to nrsc5.log.

After setting your station, click the play button to start playing the station.  
It will take about 10 seconds to begin playing if the signal stregth is good.  
Note: The settings cannot be changed while playing. 

## Album Art & Track Info
Some stations will send album art and station logos. These will be displayed in the Album Art tab if available.  
Most stations will send the song title, artist, and album. These are displayed in the Track Info pane if available.  

## Bookmarks
When a station is playing, you can click the Bookmark Station button to add it to the bookmarks list.  
You can click on the name in the bookmarks list to edit it.  
Double click the station to switch to it.  
Click the Delete Bookmark button to delete it.

## Station Info
The station name and slogan is displayed in the Info tab.  
The current audio bit rate is displayed in the Info tab. The bit rate is also shown on the status bar.

### Signal Strength
The Modulation Error Ratio for the lower and upper sidebands is displayed in the Info tab.  
High MER values for both sidebands indicates a strong signal.  
The Bit Error Rate is shown in the Info tab. High BER values will cause the audio to glitch or drop out.  
The average BER is also shown on the status bar.

## Maps
When listening to radio stations operated by [iHeartMedia](http://iheartmedia.com/iheartmedia/stations),
you can view live traffic maps and weather radar. The maps are typically sent every few minutes and
will be displayed once loaded.  
Clicking the Map Viewer button on the toolbar will open a larger window to view the maps at full size.  
The weather radar information from the last 12 hours will be stored and can be played back by
selecting the Animate Radar option. The delay between frames (in seconds) can be adjusted by changing
the Animation Speed value.

### Map Customization
The default map used for the weather radar comes from [OpenStreetMap](https://www.openstreetmap.org).
You can replace the map.png image with a map from any website that will let you export map tiles.  
The tiles used are (35,84) to (81,110) at zoom level 8. The image is 12032x6912 pixels.  
The portion of the map used for your area is cached in the map directory.
If you change the map image, you will have to delete the BaseMap images in the map directory so
they will be recreated with the new map. 

## Screenshots
![album art tab](https://raw.githubusercontent.com/cmnybo/nrsc5-gui/master/screenshots/album_art_tab.png "Album Art Tab")
![info tab](https://raw.githubusercontent.com/cmnybo/nrsc5-gui/master/screenshots/info_tab.png "Info Tab")
![settings tab](https://raw.githubusercontent.com/cmnybo/nrsc5-gui/master/screenshots/settings_tab.png "Settings Tab")

![bookmarks tab](https://raw.githubusercontent.com/cmnybo/nrsc5-gui/master/screenshots/bookmarks_tab.png "Bookmarks Tab")
![map tab](https://raw.githubusercontent.com/cmnybo/nrsc5-gui/master/screenshots/map_tab.png "Map Tab")

## Version History
1.0.0 Initial Release  
1.0.1 Fixed compatibility with display scailing  
1.1.0 Added weather radar and traffic map viewer  
