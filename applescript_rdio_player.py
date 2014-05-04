import sys
import os
import sublime
from subprocess import Popen, PIPE
from decimal import Decimal
import math

try:
    from Rdio.singleton import Singleton
except:
    from singleton import Singleton

# Wrap player interactions to compensate for different naming styles and platforms.
@Singleton
class AppleScriptRdioPlayer():
    def __init__(self):
        if sys.platform == "win32":
            raise NotImplementedError("Sorry, there's no Windows support yet.")
        elif sys.platform == "darwin": # OS X
            pass
        else:
            raise NotImplementedError("Sorry, your platform is not supported yet.")
        self.status_updater = None

    def is_running(self):
        res = self._execute_command('get running of application "Rdio"')
        return res == "true"

    def show_status_message(self):
        self.status_updater.run()

    def _get_state(self):
        return self._execute_command('tell application "Rdio" to player state')

    def is_playing(self):
        return self._get_state() == "playing"

    def is_stopped(self):
        # _get_state() never returns "stopped", just "paused", even when no music
        # is playing and nothing is queued.
        return self.get_artist() == ""

    def is_paused(self):
        return self._get_state() == "paused"

    # Current Track information
    def get_artist(self):
        return self._execute_command('tell application "Rdio" to artist of current track')

    def get_album(self):
        return self._execute_command('tell application "Rdio" to album of current track')

    def get_song(self):
        return self._execute_command('tell application "Rdio" to name of current track')

    def get_current_track(self):
        """
        Return an dict with keys "artist","album","duration","song","position" for the currently playing song.
        All with only one shell command.
        """
        result_str = self._execute_command('tell application "Rdio" to get {duration,artist,album,name} of current track & player position')
        try:
            duration, artist, album, name, position = result_str.split(", ")
            duration = int(float(duration))
            position = self._convert_position(position, duration)
        except ValueError:
            # If there's a comma in the any of the fields, there's no way to differentiate the fields
            # and a value error is thrown. In this case, just get them piece by piece.
            duration = self.get_duration()
            artist = self.get_artist()
            name = self.get_song()
            album = self.get_album()
            position = self.get_position()
        return {"duration":duration, "artist":artist, "album":album, "song":name, "position":position}

    def get_position(self):
        """ Return current position in seconds. """
        numstr = self._execute_command('tell application "Rdio" to player position')
        return self._convert_position(numstr, self.get_duration())

    def _convert_position(self, numstr, duration):
        # Rdio returns position as a percent of the total durantion.
        percent = float(numstr)
        decimalSeconds = Decimal((percent/100.0) * duration)
        if math.isnan(decimalSeconds): return 0
        return round(decimalSeconds)

    def get_duration(self):
        numstr = self._execute_command('tell application "Rdio" to duration of current track')
        return int(float(numstr))

    # Actions
    def play_pause(self):
        self._execute_command('tell application "Rdio" to playpause')

    def play_track(self, track_url, attempts=0):
        # Wait for the application to launch.
        if not self.is_running():
            if attempts > 10: return
            self._execute_command('tell application "Rdio" to launch')
            sublime.set_timeout(lambda: self.play_track(track_url, attempts+1), 1000)
        else:
            self._execute_command('tell application "Rdio" to play source "{}"'.format(track_url))
            self.show_status_message()

    def play(self, attempts=0):
        if not self.is_running():
            if attempts > 10: return
            self._execute_command('tell application "Rdio" to launch')
            sublime.set_timeout(lambda: self.play(attempts+1), 1000)
        else:
            self._execute_command('tell application "Rdio" to play')
            self.show_status_message()

    def pause(self):
        self._execute_command('tell application "Rdio" to pause')

    def next(self):
        self._execute_command('tell application "Rdio" to next track')
        self.show_status_message()

    def previous(self):
        # Call it twice - once to get back to the beginning
        # of this song and once to go back to the next.
        # This works poorly for Rdio. TODO: fix it.
        self._execute_command('tell application "Rdio" to previous track')
        self._execute_command('tell application "Rdio" to previous track')
        self.show_status_message()

    def toggle_shuffle(self):
        if self._execute_command('tell application "Rdio" to shuffle') == "true":
            self._execute_command('tell application "Rdio" to set shuffle to false')
        else:
            self._execute_command('tell application "Rdio" to set shuffle to true')

    def _execute_command(self, cmd):
        stdout = ""
        if cmd != "":
            bytes_cmd = cmd.encode('latin-1')
            p = Popen(['osascript', '-'], stdin=PIPE, stdout=PIPE, stderr=PIPE)
            stdout, stderr = p.communicate(bytes_cmd)
        return stdout.decode('utf-8').strip()
