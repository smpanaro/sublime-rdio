import sublime, sublime_plugin
from queue import Queue, Empty
import threading
import json
import time
from datetime import datetime
import random
from urllib.error import HTTPError

import sys

from Rdio.rdio import Rdio

ARTIST_TYPE = "artist"
ALBUM_TYPE = "album"
TRACK_TYPE = "track"

RDIO_ARTIST_TYPE = 'r'
RDIO_ALBUM_TYPE = 'a'
RDIO_TRACK_TYPE = 't'

RDIO_API_KEY = None
RDIO_API_SECRET = None
VALID_API_CREDENTIALS = False

try: # ST2
    from urllib.request import urlopen
    from urllib.parse import quote_plus
except: # ST3
    from urllib2 import urlopen
    from urllib import quote_plus

sublime3 = int(sublime.version()) >= 3000
if sublime3:
    from Rdio.applescript_rdio_player import AppleScriptRdioPlayer as RdioPlayer
    from Rdio.status_updater import MusicPlayerStatusUpdater
else:
    from rdio_player import RdioPlayer
    from status_updater import MusicPlayerStatusUpdater

def plugin_loaded():
    global RDIO_API_KEY, RDIO_API_SECRET, VALID_API_CREDENTIALS

    s = sublime.load_settings("Rdio.sublime-settings")
    RDIO_API_KEY = s.get("rdio_api_key")
    RDIO_API_SECRET = s.get("rdio_api_secret")

    # Test to see if the credentials are valid.
    try:
        response = Rdio((RDIO_API_KEY, RDIO_API_SECRET)).call("get", {"keys":""})
        VALID_API_CREDENTIALS = True
    except HTTPError:
        VALID_API_CREDENTIALS = False

class RdioCommand(sublime_plugin.WindowCommand):
    def __init__(self, window):
        self.window = window
        self.player = RdioPlayer.Instance()
        if not self.player.status_updater:
            self.player.status_updater = MusicPlayerStatusUpdater(self.player)

class RdioPlayCommand(RdioCommand):
    def run(self):
        self.player.play()

class RdioPauseCommand(RdioCommand):
    def run(self):
        self.player.pause()

class RdioNextTrackCommand(RdioCommand):
    def run(self):
        self.player.next()

class RdioPreviousTrackCommand(RdioCommand):
    def run(self):
        self.player.previous()

class RdioToggleShuffleCommand(RdioCommand):
    def run(self):
        self.player.toggle_shuffle()

class RdioNowPlaying(RdioCommand):
    def run(self):
        self.player.show_status_message()

class RdioSearchCommand(RdioCommand):
    """
    Handle all of the mechanics around searching.
    This includes taking input, providing and accepting suggestions from the Rdio,
    and playing the result.
    """
    def __init__(self, window):
        RdioCommand.__init__(self,window)

        self.just_opened = True
        self.typed = ""
        self.last_content = ""

        self.suggestion_selector = "→"
        self.selected_suggestion_index = None

        self.input_view_width = 0

        # Two way producer / consumer
        self.last_sent_query = ""
        self.query_q = Queue()
        self.suggestion_q = Queue()
        self.suggestions = []
        self.END_OF_SUGGESTIONS = ''
        self.STOP_THREAD_MESSAGE = 'END_OF_THREAD_TIME' # passed as a query to stop the thread

        settings = sublime.load_settings("Preferences.sublime-settings")
        self.user_tab_complete_value = settings.get("tab_completion", None)

        rdio_settings = sublime.load_settings("Rdio.sublime-settings")
        self.enable_search_suggestions = rdio_settings.get("enable_search_suggestions")

    def run(self):
        if not VALID_API_CREDENTIALS:
            sublime.error_message(
                "Sorry, search requires a valid API key and secret to work. " +
                "See the Rdio package settings (Preferences -> Package Settings -> Rdio) for more information.")
            return

        # Disable tab complete so that we can tab through suggestions.
        if self.user_tab_complete_value == False:
            settings = sublime.load_settings("Preferences.sublime-settings")
            settings.set("tab_completion", False)
            sublime.save_settings("Preferences.sublime-settings")

        self.typed = ""
        self.open_search_panel("")

        # Start search suggestion thread.
        if self.enable_search_suggestions:
            t = threading.Thread(target=self.run_search_suggestion_helper)
            t.start()

    def open_search_panel(self, content):
        tabbed = False
        self.just_opened = True
        self.last_content = content
        v = self.window.show_input_panel("Search Rdio", content, self.on_done, self.on_change, self.on_cancel)

        # Move cursor to end of query (before the suggestion text).
        content = v.substr(sublime.Region(0, v.size()))
        suggestion_start = v.find("Suggestions", 0).begin()
        if suggestion_start != -1:
            cursor_x = suggestion_start - 2
        else:
            cursor_x = max(0, v.size())
        pt = v.text_point(0, cursor_x)
        v.sel().clear()
        v.sel().add(sublime.Region(pt))
        v.show(pt)

        self.input_view_length = v.viewport_extent()[0]//v.em_width() - 1

    def on_change(self, content):
        """
        Update the search field with suggestions, if necessary.

        Specifically, intercept the newly-typed letter. If it is a tab,
        highlight the next suggestion (if there are any suggestions).
        Also, submit the current search query to the query_q Queue for
        processing by the suggestion thread. Finally, display the most
        recent search suggestion list as retrieved from the suggestion_q Queue.
        """
        # If search suggestions are disabled, we just take text input and wait for a "done" or "cancel" event.
        if not self.enable_search_suggestions:
            self.typed = content
            return

        MIN_QUERY_LENGTH = 2
        tabbed = False
        if self.just_opened:
            self.just_opened = False
            return

        # allows ctrl-a + delete
        if len(content) == 0:
            self.typed = ""
            self.open_search_panel("")
            return

        new_c = content.split(" (Suggestions")[0][-1]
        if len(self.last_content) - 1 == len(content): # then backspace
            self.typed = self.typed[:-1]
        elif len(self.last_content) > 0 and len(content) == 1: # then ctrl-a and type a new character
            self.typed = content
        elif new_c == '\t':
            tabbed = True
        else:
            self.typed += new_c

        if len(self.typed) > MIN_QUERY_LENGTH:
            self.last_sent_query = self.typed
            self.query_q.put(self.typed) # send query to every two character differences

        # Fetch the latest suggestions.
        try:
            while True:
                self.suggestions = self.suggestion_q.get_nowait()
                time.sleep(0.1)
        except Empty:
            pass

        # Try to prevent unhelpful suggestions.
        if len(self.typed) < MIN_QUERY_LENGTH:
            self.suggestions = []

        # Generate the text to display in the suggestions section.
        if tabbed and len(self.suggestions) > 0:
            if self.selected_suggestion_index is None:
                self.selected_suggestion_index = 0
            else:
                self.selected_suggestion_index += 1
                self.selected_suggestion_index %= len(self.suggestions)
            suggestion_names = list(list(zip(*(self.suggestions)))[0]) # [(a,b),(c,d),...] -> [a,c,...]
            suggestion_names[self.selected_suggestion_index] = self.suggestion_selector + suggestion_names[self.selected_suggestion_index]
            comma_separated_suggestions = ", ".join(suggestion_names)
        elif len(self.suggestions) > 0:
            suggestion_names = list(list(zip(*(self.suggestions)))[0])
            comma_separated_suggestions = ", ".join(suggestion_names)
        else:
            comma_separated_suggestions = ""

        # Reset the highlighted selection if the query is changed.
        if not tabbed:
            self.selected_suggestion_index = None

        suggestion_string = ""
        if len(comma_separated_suggestions) > 0:
            suggestion_string = " (Suggestions[TAB to select]: {})".format(comma_separated_suggestions, self.END_OF_SUGGESTIONS)

        self.open_search_panel("{}{}{}".format(self.typed, suggestion_string, self.END_OF_SUGGESTIONS))

    def on_done(self, final_query):
        self.query_q.put(self.STOP_THREAD_MESSAGE) # tell the thread to stop
        query, key = self.parse_selected_suggestion(final_query)
        if key == None:
            self.search('search', {'query':query, 'types':'Artist, Album, Track'})
        elif key.startswith(RDIO_ARTIST_TYPE):
            self.display_artist_options(query, key)
        elif key.startswith(RDIO_ALBUM_TYPE):
            self.display_album_options(query, key)
        elif key.startswith(RDIO_TRACK_TYPE):
            self.player.play_track(key)

        self.restore_tab_setting()

    def on_cancel(self):
        self.query_q.put(self.STOP_THREAD_MESSAGE) # tell the thread to stop
        self.restore_tab_setting()

    def restore_tab_setting(self):
        """
        Restore tab complete settings now that we're done. Only write them if the
        user explicitly set them to False, since the default is True.
        """
        if self.user_tab_complete_value == False:
            settings = sublime.load_settings("Preferences.sublime-settings")
            settings.set("tab_completion", False)
            sublime.save_settings("Preferences.sublime-settings")

    def parse_selected_suggestion(self, final_query):
        """
        Returns a tuple (query, key) depending on the contents of self.suggestions.
        key is a Rdio key or None of the query is just a search query.
        """
        query = self.typed
        key = None
        if self.suggestion_selector in final_query:
            query = self.suggestions[self.selected_suggestion_index][0]
            key = self.suggestions[self.selected_suggestion_index][1]
        return (query, key)

    def get_suggestions(self, rdio_results):
        MAX_TEXT_LENGTH = self.input_view_length - len(self.typed) - len(" (Suggestions[TAB to select]: )") - 2
        suggestions = []
        for res in rdio_results['result']:
            if res['type'] == RDIO_ARTIST_TYPE:
                artist = res.get("name", None)
                key = res.get("key", None)
                t = (artist,key)
                if artist and t not in suggestions:
                    suggestions.append(t)
            elif res['type'] == RDIO_ALBUM_TYPE:
                album = res.get("name", None)
                key = res.get("key", None)
                t = (album,key)
                if album and t not in suggestions:
                    suggestions.append(t)
            elif res['type'] == RDIO_TRACK_TYPE:
                track = res.get("name", None)
                key = res.get("key", None)
                t = (track,key)
                if track and t not in suggestions:
                    suggestions.append(t)
            else:
                continue #ignore other types of results
            s_list = list(zip(*suggestions))[0]
            if len(", ".join(s_list)) > MAX_TEXT_LENGTH:
                suggestions.pop()
                break
        return suggestions

    def run_search_suggestion_helper(self):
        """
        Reads from the self.query_q Queue and searches the Rdio suggestions API.
        Places the results in the self.suggestions_q Queue in the form of: [("{suggestion}", "{Rdio Key}")]
        """
        rdio = Rdio((RDIO_API_KEY, RDIO_API_SECRET))

        new_query = ""
        last_query = None
        while True:
            try:
                while True: #Empty the queue.
                    new_query = self.query_q.get_nowait()
                    time.sleep(0.1) # very important, else these while True loops hog the cpu.
            except Empty:
                pass

            if new_query == self.STOP_THREAD_MESSAGE: break

            if new_query != last_query:
                last_query = new_query
                response = rdio.call('searchSuggestions', {'query':new_query})
                suggestions = self.get_suggestions(response)
                self.suggestion_q.put(suggestions)
            time.sleep(0.1)

    def display_artist_options(self, query, key):
        self.window.show_quick_panel(["Songs by " + query, "Albums by " + query], lambda idx: self.handle_artist_selection(idx, key))

    def handle_artist_selection(self, index, key):
        if index == 0:
            self.search("getTracksForArtist", {"artist":key, "count":"50"})
        if index == 1:
            self.search("getAlbumsForArtist", {"artist":key, "count":"20"})

    def display_album_options(self, query, key):
        self.window.show_quick_panel(["Play " + query, "Show tracks on " + query], lambda idx: self.handle_album_selection(idx, key))

    def handle_album_selection(self, index, key):
        if index == 0:
            self.player.play_track(key)
        if index == 1:
            track_thread = ThreadedRdioTrackRequest(key, self)
            track_thread.setDaemon(True)
            track_thread.start()

    def search(self, query, params):
        url_thread = ThreadedRdioSearchRequest(query, params, self)
        url_thread.setDaemon(True)
        url_thread.start()

    def handle_search_response(self, method, response, error_message):
        """ Parse the various types of searches and display the results in the quick panel. """

        MAX_RESULTS = 50
        if error_message is not None:
            sublime.error_message("Unable to search:\n%s" % error_message)
            return

        if (method == "search" and response["result"]["number_results"] == 0) or \
           (method == "getTracksForArtist" and len(response["result"]) == 0)  or \
           (method == "getAlbumsForArtist" and len(response["result"]) == 0)  or \
           (method == "getTracksForAlbum" and len(response) == 0):
            self.open_search_panel("No results found, try again?")
            return

        if method == "search":
            results = response["result"]["results"]
        elif method == "getTracksForArtist" or method == "getAlbumsForArtist":
            results = response["result"]
        elif method == "getTracksForAlbum":
            results = response

        rows = []
        self.rdio_keys = []
        self.result_names = [] # for use in further dialogs

        for r in results:
            if r['type'] == RDIO_TRACK_TYPE:
                song = r.get("name","")
                artists = r.get("artist","")
                album = r.get("album", "")
                rows.append([u"{0} by {1}".format(song, artists), u"{0}".format(album)])
                self.result_names.append(song)
                self.rdio_keys.append(r.get("key", ""))
            elif r['type'] == RDIO_ALBUM_TYPE:
                name = r.get("name","")
                artists = r.get("artist", "")
                num_tracks = r.get("length", "")
                rows.append([u"{0} [Album]".format(name), u"by {0}".format(artists)])
                self.result_names.append(name)
                self.rdio_keys.append(r.get("key", ""))
            elif r['type'] == RDIO_ARTIST_TYPE:
                name = r.get("name", "")
                rows.append([u"{0} [Artist]".format(name),""])
                self.result_names.append(name)
                self.rdio_keys.append(r.get("key", ""))
            if len(rows) > MAX_RESULTS: break
        self.window.show_quick_panel(rows, self.handle_search_quick_panel_selection)

    def handle_search_quick_panel_selection(self, index):
        if index == -1: return # dialog was cancelled
        key = self.rdio_keys[index]
        if key.startswith(RDIO_ALBUM_TYPE):
            sublime.set_timeout(lambda: self.display_album_options(self.result_names[index], key), 10)
        elif key.startswith(RDIO_ARTIST_TYPE):
            sublime.set_timeout(lambda: self.display_artist_options(self.result_names[index], key), 10)
        else:
            self.player.play_track(key)

class ThreadedRdioSearchRequest(threading.Thread):
    """ Given a Rdio API method and parameters, return the response via a callback. """

    def __init__(self, method, params, caller):
        threading.Thread.__init__(self)
        self.method = method
        self.params = params
        self.caller = caller
        self.rdio = Rdio((RDIO_API_KEY, RDIO_API_SECRET))

    def run(self):
        error = None
        try:
            response = self.rdio.call(self.method, self.params)
            if response['status'] != 'ok': error = "Rdio internal server error."
        except e:
            response = None
            error = e

        # Start playing on the main thread.
        sublime.set_timeout(lambda: self.caller.handle_search_response(self.method, response, error), 10)

class ThreadedRdioTrackRequest(threading.Thread):
    """ Given a Rdio album key (e.g. "a123123") and a caller, returns a list of track information via a callback. """

    def __init__(self, album_key, caller):
        threading.Thread.__init__(self)
        self.album_key = album_key
        self.caller = caller
        self.rdio = Rdio((RDIO_API_KEY, RDIO_API_SECRET))

    def run(self):
        error = None
        try:
            album_response = self.rdio.call("get", {"keys":self.album_key})
            track_keys = album_response["result"][self.album_key]["trackKeys"]
            track_response = self.rdio.call("get", {"keys":", ".join(track_keys)})
            response = []
            for k in track_keys:
                response.append(track_response["result"][k])
        except e:
            response = None
            error = e

        # Start playing on the main thread.
        sublime.set_timeout(lambda: self.caller.handle_search_response("getTracksForAlbum", response, error), 10)


