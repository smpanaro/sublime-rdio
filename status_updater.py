# encoding: utf-8
from __future__ import unicode_literals

import sublime
import random

sublime3 = int(sublime.version()) >= 3000
if sublime3:
    set_timeout_async = sublime.set_timeout_async
else:
    set_timeout_async = sublime.set_timeout

class MusicPlayerStatusUpdater():
    def __init__(self, player):
        self.player = player

        s = sublime.load_settings("Rdio.sublime-settings")
        self.display_duration = int(s.get("status_duration"))
        self.status_format = s.get("status_format")

        self.current_song = None
        self.current_artist = None
        self.current_album = None
        self.current_duration = None

        self._update_delay = int(s.get("status_update_period")) # Udpate every n milliseconds.
        self._cycles_left = self.display_duration * 1000 // self._update_delay

        self.bars = ["▁","▂","▄","▅"]

        self._is_displaying = False
        if self.display_duration < 0 and self.player.is_running(): self.run()

    def _get_min_sec_string(self,seconds):
        m = seconds//60
        s = seconds - 60*m
        return "%d:%.02d" % (m,s)

    def _get_message(self):

        if self.player.is_playing():
            icon = "►"
            random.shuffle(self.bars)
        else:
            icon = "∣∣"

        current_song_info = self.player.get_current_track()
        self.current_song = current_song_info.get("song","")
        self.current_artist = current_song_info.get("artist","")
        self.current_album = current_song_info.get("album","")
        self.current_duration_secs = current_song_info.get("duration","")
        self.current_position_secs = current_song_info.get("position","")

        return self.status_format.format(
            equalizer="".join(self.bars),
            icon=icon,
            time=self._get_min_sec_string(self.current_position_secs),
            duration=self._get_min_sec_string(self.current_duration_secs),
            song=self.current_song,
            artist=self.current_artist,
            album=self.current_album)

    def run(self):
        if not self._is_displaying:
            self._is_displaying = True
            self._run()

    def _run(self):
        if self._cycles_left == 0:
            sublime.status_message("")
            self._cycles_left = self.display_duration * 1000 / self._update_delay
            self._is_displaying = False
            return
        elif self._cycles_left > 0:
            self._cycles_left -= 1

        if self.player.is_running() and not self.player.is_stopped():
            sublime.status_message(self._get_message())
            set_timeout_async(lambda: self._run(), self._update_delay)
        else:
            sublime.status_message("")
            self._is_displaying = False
