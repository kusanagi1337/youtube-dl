# coding: utf-8
from __future__ import unicode_literals

import functools
import hashlib
import itertools
import json
import re

from .common import InfoExtractor
from ..compat import (
    compat_HTTPError,
    compat_kwargs,
    compat_parse_qs,
    compat_str,
    compat_urllib_error,
    compat_urllib_parse_urlparse,
    compat_urlparse,
)
from ..utils import (
    error_to_compat_str,
    ExtractorError,
    OnDemandPagedList,
    clean_html,
    dict_get,
    float_or_none,
    extract_attributes,
    get_element_by_class,
    int_or_none,
    js_to_json,
    merge_dicts,
    parse_bitrate,
    parse_duration,
    parse_iso8601,
    strip_or_none,
    try_get,
    unescapeHTML,
    unified_timestamp,
    url_or_none,
    urlencode_postdata,
    urljoin,
)


def _with_ident_re(x):
    return r'(?P<pre>BBC\s+\w+\s+-\s+)?%s(?(pre)|:\s+-\s+BBC\s+\w+)?' % (re.escape(x), )


class BBCBaseIE(InfoExtractor):
    _ID_REGEX = r'(?:[pbml][\da-z]{7}|w[\da-z]{7,14})'
    _URL_TEMPLATE = 'http://www.bbc.co.uk/programmes/%s'

    _NETRC_MACHINE = 'bbc'

    # full version with hash needed for live streams
    _MEDIA_SELECTOR_URL_TEMPL = 'https://open.live.bbc.co.uk/mediaselector/6/select/version/2.0/mediaset/%s/vpid/%s/format/json/atk/%s/asn/1/'

    _MEDIA_SETS = [
        'pc',
        'mobile-table-main',
    ]

    _EMP_PLAYLIST_NS = 'http://bbc.co.uk/2008/emp/playlist'

    _DESCRIPTION_KEY = 'synopses'

    class MediaSelectionError(Exception):
        def __init__(self, id):
            self.id = id

    def _extract_medias(self, media_selection):
        error = media_selection.get('result')
        if error:
            raise self.MediaSelectionError(error)
        return media_selection.get('media') or []

    def _extract_connections(self, media):
        return media.get('connection') or []

    def _get_description(self, data, sizes=None):
        synopses = try_get(data, lambda x: x[self._DESCRIPTION_KEY], dict) or {}
        return strip_or_none(dict_get(synopses, ('large', 'medium', 'small') if sizes is None else sizes)) or None

    def _raise_extractor_error(self, other_error):
        raise ExtractorError(
            '%s returned error: %s' % (
                self.IE_NAME,
                other_error.id if isinstance(other_error, self.MediaSelectionError)
                else error_to_compat_str(other_error)),
            expected=True)

    @classmethod
    def _hash_vpid(cls, vpid):
        return hashlib.sha1(('7dff7671d0c697fedb1d905d9a121719938b92bf' + vpid).encode('ascii')).hexdigest()

    def _download_media_selector(self, programme_id):
        last_exception = None
        formats, subtitles = [], {}
        for media_set in self._MEDIA_SETS:
            try:
                fmts, sttls = self._download_media_selector_url(
                    self._MEDIA_SELECTOR_URL_TEMPL % (
                        media_set, programme_id,
                        self._hash_vpid(programme_id)),
                    programme_id)
                formats.extend(fmts)
                if sttls:
                    subtitles = self._merge_subtitles(subtitles, sttls)
            except self.MediaSelectionError as e:
                if e.id in ('notukerror', 'geolocation', 'selectionunavailable'):
                    last_exception = e
                    continue
                self._raise_extractor_error(e)
        if last_exception:
            if formats or subtitles:
                if last_exception.id != 'selectionunavailable':
                    self.report_warning(
                        '%s returned error: %s' % (self.IE_NAME, last_exception.id))
            else:
                self._raise_extractor_error(last_exception)
        return formats, subtitles

    def _download_media_selector_url(self, url, programme_id=None):
        media_selection = self._download_json(
            url, programme_id, 'Downloading media selection JSON',
            expected_status=(403, 404))
        return self._process_media_selector(media_selection, programme_id)

    def _process_media_selector(self, media_selection, programme_id):
        formats = []
        subtitles = None
        urls = set([])

        for media in self._extract_medias(media_selection):
            kind = media.get('kind')
            if kind in ('video', 'audio'):
                bitrate = int_or_none(media.get('bitrate'))
                encoding = media.get('encoding')
                width = int_or_none(media.get('width'))
                height = int_or_none(media.get('height'))
                file_size = int_or_none(media.get('media_file_size'))
                for connection in self._extract_connections(media):
                    href = connection.get('href')
                    if href in urls:
                        continue
                    if href:
                        urls.add(href)
                    conn_kind = connection.get('kind')
                    protocol = connection.get('protocol')
                    supplier = connection.get('supplier')
                    transfer_format = connection.get('transferFormat')
                    format_id = supplier or conn_kind or protocol
                    # ASX playlist
                    if supplier == 'asx':
                        for i, ref in enumerate(self._extract_asx_playlist(connection, programme_id)):
                            formats.append({
                                'url': ref,
                                'format_id': 'ref%s_%s' % (i, format_id),
                            })
                    elif transfer_format == 'dash':
                        formats.extend(self._extract_mpd_formats(
                            href, programme_id, mpd_id=format_id, fatal=False))
                    elif transfer_format == 'hls':
                        # TODO: let expected_status be passed into _extract_xxx_formats() instead
                        try:
                            fmts = self._extract_m3u8_formats(
                                href, programme_id, ext='mp4', entry_protocol='m3u8_native',
                                m3u8_id=format_id, fatal=False)
                        except ExtractorError as e:
                            if not (isinstance(e.exc_info[1], compat_urllib_error.HTTPError)
                                    and e.exc_info[1].code in (403, 404)):
                                raise
                            fmts = []
                        formats.extend(fmts)
                    elif transfer_format == 'hds':
                        formats.extend(self._extract_f4m_formats(
                            href, programme_id, f4m_id=format_id, fatal=False))
                    else:
                        if not supplier and bitrate:
                            format_id += '-%d' % bitrate
                        fmt = {
                            'format_id': format_id,
                            'filesize': file_size,
                        }
                        if kind == 'video':
                            fmt.update({
                                'width': width,
                                'height': height,
                                'tbr': bitrate,
                                'vcodec': encoding,
                            })
                        else:
                            fmt.update({
                                'abr': bitrate,
                                'acodec': encoding,
                                'vcodec': 'none',
                            })
                        if protocol in ('http', 'https'):
                            # Direct link
                            fmt.update({
                                'url': href,
                            })
                        elif protocol == 'rtmp':
                            application = connection.get('application', 'ondemand')
                            auth_string = connection.get('authString')
                            identifier = connection.get('identifier')
                            server = connection.get('server')
                            fmt.update({
                                'url': '%s://%s/%s?%s' % (protocol, server, application, auth_string),
                                'play_path': identifier,
                                'app': '%s?%s' % (application, auth_string),
                                'page_url': 'http://www.bbc.co.uk',
                                'player_url': 'http://www.bbc.co.uk/emp/releases/iplayer/revisions/617463_618125_4/617463_618125_4_emp.swf',
                                'rtmp_live': False,
                                'ext': 'flv',
                            })
                        else:
                            continue
                        formats.append(fmt)
            elif kind == 'captions':
                subtitles = self.extract_subtitles(media, programme_id)
        return formats, subtitles

    def _preload_state(self, webpage, video_id):
        return self._parse_json(self._search_regex(
            r'(?s)window\.__PRELOADED_STATE__\s*=\s*({.+?});', webpage,
            'preload state', default='{}'), video_id, fatal=False)

    def _redux_state(self, webpage, video_id):
        redux_state_json = self._search_regex(
            r'''<script\b[^>]+\bid\s*=\s*(["'])tvip-script-app-store\1[^>]*>[^<]*_REDUX_STATE__\s*=\s*(?P<json>[^<]+)\s*;\s*<''',
            webpage, 'redux_state', default='{}', group='json')
        return self._parse_json(redux_state_json, video_id, transform_source=unescapeHTML, fatal=False)

    def _page_error(self, webpage):
        return None

    def _download_video_page(self, url_or_request, video_id, *args, **kwargs):

        if 'expected_status' not in kwargs:
            kwargs['expected_status'] = 404
            kwargs = compat_kwargs(kwargs)

        webpage, urlh = self._download_webpage_handle(url_or_request, video_id, *args, **kwargs)

        error = self._page_error(webpage)
        if not error and urlh.getcode() == 404:
            error = compat_urllib_error.HTTPError(urlh.geturl(), 404, 'Not Found', urlh.info(), urlh)
        if error:
            self._raise_extractor_error(error)

        return webpage

    def report_extraction(self, id_or_name, target=None):
        self.to_screen('%s: Extracting %s' % (id_or_name, ('from ' + target) if target else 'information'))


class BBCCoUkIE(BBCBaseIE):
    IE_NAME = 'bbc.co.uk'
    IE_DESC = 'BBC iPlayer'
    _VALID_URL = r'''(?x)
                    https?://
                        (?:www\.)?bbc\.co\.uk/
                        (?:
                            programmes/(?!articles/)|
                            iplayer(?:/[^/]+)?/(?:episode|playlist)/|
                            music/(?:clips|audiovideo/popular)[/#]|
                            radio/player/|
                            sounds/play/|
                            events/[^/]+/play/[^/]+/|
                            (?:iplayer|(?P<radio>sounds/play))/(?P<live>live)(?(radio):|/)
                        )
                        (?P<id>(?(live)bbc\w+|%s))(?!/(?:episodes|broadcasts|clips))
                    ''' % BBCBaseIE._ID_REGEX

    _LOGIN_URL = 'https://account.bbc.com/signin'

    _MEDIA_SETS = [
        # Provides HQ HLS streams with even better quality that pc mediaset but fails
        # with geolocation in some cases when it's even not geo restricted at all (eg
        # http://www.bbc.co.uk/programmes/b06bp7lf). Also may fail with selectionunavailable.
        'iptv-all',
        'pc',
    ]

    _TESTS = [
        {
            'url': 'http://www.bbc.co.uk/programmes/b039g8p7',
            'info_dict': {
                'id': 'b039d07m',
                'ext': 'mp4',
                'title': 'Kaleidoscope, Leonard Cohen',
                'description': 'The Canadian poet and songwriter reflects on his musical career.',
                'timestamp': 785879520,
                'upload_date': '19941126',
            },
        },
        {
            'url': 'http://www.bbc.co.uk/iplayer/episode/b00yng5w/The_Man_in_Black_Series_3_The_Printed_Name/',
            'info_dict': {
                'id': 'b00yng1d',
                'ext': 'mp4',
                'title': 'The Man in Black: Series 3: The Printed Name',
                'description': "Mark Gatiss introduces Nicholas Pierpan's chilling tale of a writer's devilish pact with a mysterious man. Stars Ewan Bailey.",
                'duration': 1800,
            },
            'skip': 'Sorry, this episode is not currently available',
        },
        {
            'url': 'http://www.bbc.co.uk/iplayer/episode/b03vhd1f/The_Voice_UK_Series_3_Blind_Auditions_5/',
            'info_dict': {
                'id': 'b00yng1d',
                'ext': 'flv',
                'title': 'The Voice UK: Series 3: Blind Auditions 5',
                'description': 'Emma Willis and Marvin Humes present the fifth set of blind auditions in the singing competition, as the coaches continue to build their teams based on voice alone.',
                'duration': 5100,
            },
            'skip': 'Sorry, this episode is not currently available',
        },
        {
            'url': 'http://www.bbc.co.uk/iplayer/episode/p026c7jt/tomorrows-worlds-the-unearthly-history-of-science-fiction-2-invasion',
            'info_dict': {
                'id': 'b03k3pb7',
                'ext': 'mp4',
                'title': "Tomorrow's Worlds: The Unearthly History of Science Fiction",
                'description': '2. Invasion',
                'duration': 3600,
            },
            'skip': 'Sorry, this episode is not currently available',
        }, {
            'url': 'http://www.bbc.co.uk/programmes/b04v20dw',
            'info_dict': {
                'id': 'b04v209v',
                'ext': 'mp4',
                'title': 'Pete Tong, The Essential New Tune Special',
                'description': "Pete has a very special mix - all of 2014's Essential New Tunes!",
                'duration': 10800,
            },
            'skip': 'Sorry, this episode is not currently available',
        }, {
            'url': 'https://www.bbc.co.uk/sounds/play/p022h44b',
            'note': 'Audio',
            'info_dict': {
                'id': 'p022h44j',
                'ext': 'mp4',
                'title': 'BBC Proms Music Guides - Rachmaninov: Symphonic Dances',
                'description': "In this Proms Music Guide, Andrew McGregor looks at Rachmaninov's Symphonic Dances.",
                'timestamp': 1404900077,
                'upload_date': '20140709',
                'duration': 227,
            },
        }, {
            'url': 'https://www.bbc.co.uk/events/e65q2m/play/a6hrbp/p025c0zz',
            'note': 'Video',
            'info_dict': {
                'id': 'p025c103',
                'ext': 'mp4',
                'title': r're:(?:.* )?Reading and Leeds Festival, 2014, Rae Morris - Closer \(Live on BBC Three\)',
                'description': 'Rae Morris performs Closer for BBC Three at Reading 2014',
                'duration': 226,
            },
        }, {
            'url': 'http://www.bbc.co.uk/iplayer/episode/b054fn09/ad/natural-world-20152016-2-super-powered-owls',
            'info_dict': {
                'id': 'p02n76xf',
                'ext': 'mp4',
                'title': 'Natural World, 2015-2016: 2. Super Powered Owls',
                'description': 'md5:e4db5c937d0e95a7c6b5e654d429183d',
                'duration': 3540,
            },
            'skip': 'Sorry, this episode is not currently available',
        }, {
            'url': 'http://www.bbc.co.uk/iplayer/episode/b05zmgwn/royal-academy-summer-exhibition',
            'info_dict': {
                'id': 'b05zmgw1',
                'ext': 'mp4',
                'description': 'Kirsty Wark and Morgan Quaintance visit the Royal Academy as it prepares for its annual artistic extravaganza, meeting people who have come together to make the show unique.',
                'title': 'Royal Academy Summer Exhibition',
                'duration': 3540,
            },
            'skip': 'Sorry, this episode is not currently available',
        }, {
            # iptv-all mediaset fails with geolocation; however there is no geo restriction
            # for this programme at all
            'url': 'http://www.bbc.co.uk/programmes/b06rkn85',
            'info_dict': {
                'id': 'b06rkms3',
                'ext': 'mp4',
                'title': "Best of the Mini-Mixes 2015: Part 3, Annie Mac's Friday Night - BBC Radio 1",
                'description': "Annie has part three in the Best of the Mini-Mixes 2015, plus the year's Most Played!",
            },
            'skip': 'Sorry, this episode is not currently available',
        }, {
            # compact player (https://github.com/ytdl-org/youtube-dl/issues/8147)
            'url': 'http://www.bbc.co.uk/programmes/p028bfkf/player',
            'info_dict': {
                'id': 'p028bfkj',
                'ext': 'mp4',
                'title': 'Extract from BBC documentary Look Stranger - Giant Leeks and Magic Brews',
                'description': 'Extract from BBC documentary Look Stranger - Giant Leeks and Magic Brews',
            },
            'skip': 'Sorry, this clip is not currently available',
        }, {
            'url': 'https://www.bbc.co.uk/sounds/play/m0007jzb',
            'note': 'Audio',
            'info_dict': {
                'id': 'm0007jz9',
                'ext': 'mp4',
                'title': 'BBC Proms, 2019, Prom 34: West–Eastern Divan Orchestra',
                'description': "Live BBC Proms. West–Eastern Divan Orchestra with Daniel Barenboim and Martha Argerich.",
                'duration': 9840,
            },
            'params': {
                # rtmp download
                'skip_download': True,
            },
            'skip': 'Sorry, the page you are looking for cannot be found!',
        }, {
            'url': 'http://www.bbc.co.uk/iplayer/playlist/p01dvks4',
            'only_matching': True,
        }, {
            'url': 'http://www.bbc.co.uk/music/clips#p02frcc3',
            'only_matching': True,
        }, {
            'url': 'http://www.bbc.co.uk/iplayer/cbeebies/episode/b0480276/bing-14-atchoo',
            'only_matching': True,
        }, {
            'url': 'http://www.bbc.co.uk/radio/player/p03cchwf',
            'only_matching': True,
        }, {
            'url': 'https://www.bbc.co.uk/music/audiovideo/popular#p055bc55',
            'only_matching': True,
        }, {
            'url': 'http://www.bbc.co.uk/programmes/w3csv1y9',
            'only_matching': True,
        }, {
            'url': 'https://www.bbc.co.uk/programmes/m00005xn',
            'only_matching': True,
        }, {
            'url': 'https://www.bbc.co.uk/programmes/w172w4dww1jqt5s',
            'only_matching': True,
        }, {
            'note': 'original programme',
            'url': 'https://www.bbc.co.uk/iplayer/episode/m000b1v0/his-dark-materials-series-1-1-lyras-jordan',
            'info_dict': {
                'id': 'm000b1tz',
                'ext': 'mp4',
                'title': 'His Dark Materials - Series 1: 1. Lyra\u2019s Jordan',
                'description': 'Orphan Lyra Belacqua\'s world is turned upside-down by her long-absent uncle\'s return from the north, while the glamorous Mrs Coulter visits Jordan College with a proposition.',
                'duration': 3407,
            },
            'params': {
                'skip_download': True,
            },
            # 'skip': 'geolocation',
        }, {
            'note': 'audio-described programme',
            'url': 'https://www.bbc.co.uk/iplayer/episode/m000b1v0/ad/his-dark-materials-series-1-1-lyras-jordan',
            'info_dict': {
                'id': 'p07ss5kj',
                'ext': 'mp4',
                'title': 'His Dark Materials - Series 1: 1. Lyra\u2019s Jordan - Audio Described',
                'description': 'Orphan Lyra Belacqua\'s world is turned upside-down by her long-absent uncle\'s return from the north, while the glamorous Mrs Coulter visits Jordan College with a proposition.',
                'duration': 3407,
            },
            'params': {
                'skip_download': True,
            },
            # 'skip': 'geolocation',
        }, {
            'note': 'Live TV',
            'url': 'https://www.bbc.co.uk/iplayer/live/bbcnews',
            'info_dict': {
                'id': 'bbc_news24',
                'ext': 'mp4',
                'title': r're:\S+.*?\S \d{4}-[01]?\d-[0-3]?\d [0-2]\d:[0-5]?\d$',
                'description': compat_str,
                'timestamp': int,
                'upload_date': r're:\d{8}',
                'is_live': True,
            },
            'params': {
                'skip_download': True,
            },
        }, {
            'note': 'Live radio',
            'url': 'https://www.bbc.co.uk/sounds/play/live:bbc_world_service',
            'info_dict': {
                'id': 'bbc_world_service',
                'ext': 'mp4',
                'title': r're:\S+.*?\S \d{4}-[01]?\d-[0-3]?\d [0-2]\d:[0-5]?\d$',
                'description': compat_str,
                'timestamp': int,
                'upload_date': r're:\d{8}',
                'episode': compat_str,
                'is_live': True,
            },
            'params': {
                'skip_download': True,
            },
        }]

    def _login(self):
        username, password = self._get_login_info()
        if username is None:
            return
        login_page = self._download_webpage(
            self._LOGIN_URL, None, 'Downloading signin page')

        login_form = self._hidden_inputs(login_page)

        login_form.update({
            'username': username,
            'password': password,
        })

        post_url = urljoin(self._LOGIN_URL, self._search_regex(
            r'<form[^>]+action=(["\'])(?P<url>.+?)\1', login_page,
            'post url', default=self._LOGIN_URL, group='url'))

        response, urlh = self._download_webpage_handle(
            post_url, None, 'Logging in', data=urlencode_postdata(login_form),
            headers={'Referer': self._LOGIN_URL})

        if self._LOGIN_URL in urlh.geturl():
            error = clean_html(get_element_by_class('form-message', response))
            if error:
                raise ExtractorError(
                    'Unable to login: %s' % error, expected=True)
            raise ExtractorError('Unable to log in')

    def _real_initialize(self):
        self._login()

    def _extract_asx_playlist(self, connection, programme_id):
        asx = self._download_xml(connection.get('href'), programme_id, 'Downloading ASX playlist')
        return [ref.get('href') for ref in asx.findall('./Entry/ref')]

    def _extract_items(self, playlist):
        return playlist.findall('./{%s}item' % self._EMP_PLAYLIST_NS)

    @staticmethod
    def _get_programme_from_playlist_data(pl_data):
        """pl_data: list of dict"""
        return next((x for x in pl_data or [] if x.get('kind') in ('programme', 'radioProgramme')), None)

    def _download_playlist(self, playlist_id):
        try:
            playlist = self._download_json(
                'http://www.bbc.co.uk/programmes/%s/playlist.json' % playlist_id,
                playlist_id, 'Downloading playlist JSON')

            formats, subtitles = [], {}
            programme_id = title = description = duration = None
            for version in playlist.get('allAvailableVersions', []):
                smp_config = try_get(version, lambda x: x['smpConfig'], dict)
                if not smp_config:
                    continue
                title = smp_config.get('title')
                if not title:
                    continue
                description = smp_config.get('summary')
                thumbnail = smp_config.get('holdingImageURL')
                item = self._get_programme_from_playlist_data(smp_config.get('items'))
                if item is not None:
                    # must be set?
                    programme_id = item.get('vpid')
                    duration = int_or_none(item.get('duration'))
                    version_formats, version_subtitles = self._download_media_selector(programme_id)
                    types = version.get('types', ['unknown'])
                    for f in version_formats:
                        f['format_note'] = ', '.join(types)
                        if any('AudioDescribed' in x for x in types):
                            f['language_preference'] = -10
                    formats += version_formats
                    if version_subtitles:
                        subtitles = self._merge_subtitles(subtitles, version_subtitles)

            if formats:
                return programme_id, title, description, duration, formats, subtitles, thumbnail
        except ExtractorError as ee:
            if not (isinstance(ee.cause, compat_HTTPError) and ee.cause.code == 404):
                raise

        # fallback to legacy playlist
        return self._process_legacy_playlist(playlist_id)

    def _process_legacy_playlist_url(self, url, display_id):
        playlist = self._download_legacy_playlist_url(url, display_id)
        return self._extract_from_legacy_playlist(playlist, display_id)

    def _process_legacy_playlist(self, playlist_id):
        return self._process_legacy_playlist_url(
            'http://www.bbc.co.uk/iplayer/playlist/%s' % playlist_id, playlist_id)

    def _download_legacy_playlist_url(self, url, playlist_id=None):
        return self._download_xml(
            url, playlist_id, 'Downloading legacy playlist XML')

    def _extract_from_legacy_playlist(self, playlist, playlist_id):
        no_items = playlist.find('./{%s}noItems' % self._EMP_PLAYLIST_NS)
        if no_items is not None:
            reason = no_items.get('reason')
            msg = {
                'preAvailability': 'Episode %s is not yet available',
                'postAvailability': 'Episode %s is no longer available',
                'noMedia': 'Episode %s is not currently available',
            }.get(reason, 'Episode %s is not available: ' + reason) % (playlist_id, )
            self._raise_extractor_error(msg)

        def get_programme_id(item):

            def get_from_attributes(item):
                for p in ('identifier', 'group'):
                    value = item.get(p, '')
                    if re.match(self._ID_REGEX, value):
                        return value

            value = get_from_attributes(item)
            if value:
                return value
            mediator = item.find('./{%s}mediator' % self._EMP_PLAYLIST_NS)
            if mediator is not None:
                return get_from_attributes(mediator)

        item = self._get_programme_from_playlist_data(self._extract_items(playlist))
        if item is not None:
            title_el = playlist.find('./{%s}title' % self._EMP_PLAYLIST_NS)
            if title_el is None:
                return
            title = title_el.text
            description_el = playlist.find('./{%s}summary' % self._EMP_PLAYLIST_NS)
            description = description_el.text if description_el is not None else None

            programme_id = get_programme_id(item)
            if programme_id:
                formats, subtitles = self._download_media_selector(programme_id)
            else:
                formats, subtitles = self._process_media_selector(item, playlist_id)
                programme_id = playlist_id

            duration = int_or_none(item.get('duration'))
            return programme_id, title, description, duration, formats, subtitles, None

    def _page_error(self, webpage):
        return self._html_search_regex(
            r'<\w+\b[^>]+\bclass\s*=\s*(["\'])(?=.*\b(?:(?:smp|playout)__message delta|play-c-error__title)\b)(?:(?!\1).)+\1[^>]*>(?P<msg>[^<]+?)<',
            webpage, 'error', group='msg', default=None)

    def _real_extract(self, url):
        group_id, live = re.match(self._VALID_URL, url).group('id', 'live')
        radio = '/sounds/play/' in url

        webpage = self._download_video_page(url, group_id, 'Downloading video page')

        programme_id = title = title2 = ep_title = None
        timestamp = duration = description = thumbnail = None

        def make_title(title, title2=None):
            if title and title2:
                title += ' - ' + title2
            return title or title2 or None

        def get_duple(dct, names=('primary', 'secondary')):
            return tuple(dct.get(k) for k in names) or (None, None)

        if radio:
            preload_state = self._preload_state(webpage, group_id)
            self.report_extraction(group_id, '__PRELOADED_STATE__')
            player = try_get(preload_state, lambda x: x['modules']['data'], list)
            player = try_get(player, lambda x: next(y['data'][0] for y in x if y.get('title') == 'Player'), dict)
            programme_id = player['urn'].rsplit(':')[-1]
            episode = self._download_json(
                'http://www.bbc.co.uk/programmes/%s.json' % (programme_id, ),
                programme_id, 'Downloading programme JSON', fatal=False) or {}
            episode = episode.get('programme') or {}
            version = try_get(episode, lambda x: next((y for y in x['versions'] if y['pid'] == programme_id), x['versions'][0]), dict) or {}
            title, title2 = try_get(player, lambda x: get_duple(x['titles']))
            if live:
                ep_title = make_title(title, title2)
                title, title2 = try_get(preload_state, lambda x: get_duple(x['programmes']['current']['titles']))
            else:
                episode = player
                description = self._get_description(episode)
                timestamp = parse_iso8601(
                    try_get(episode,
                            (lambda x: x['release']['date'],
                             lambda x: x['availability']['from']),
                            compat_str))
                duration = try_get(episode, lambda x: x['duration']['value'], int)
                programme_id = version.get('pid') or programme_id
            thumbnail = player.get('image_url')
        else:
            # current pages embed data from http://www.bbc.co.uk/programmes/PID.json
            # similar data available at http://ibl.api.bbc.co.uk/ibl/v1/episodes/PID
            redux_state = self._redux_state(webpage, group_id)
            if live:
                if redux_state:
                    self.report_extraction(group_id, '*_REDUX_STATE__')
                group_id = try_get(redux_state, lambda x: x['channel']['id'], compat_str) or group_id
                episode_id, programme_id = try_get(redux_state, lambda x: (x['broadcasts']['items'][0][k] for k in ('episodeId', 'versionId')))
                episode = self._download_json(
                    'http://www.bbc.co.uk/programmes/%s.json' % (episode_id, ),
                    programme_id, 'Downloading programme JSON', fatal=False) or {}
                episode = episode.get('programme') or {}
                title, title2 = try_get(
                    episode,
                    (lambda x: get_duple(x['display_title'], ('title', 'subtitle')),
                     lambda x: (x['title'], None)))

                version = try_get(episode, lambda x: next(y for y in x['versions'] if y['pid'] == programme_id), dict) or {}
                thumbnail = try_get(episode, lambda x: 'https://ichef.bbci.co.uk/images/ic/{recipe}/%s.jpg' % (x['image']['pid'], ), compat_str)
            else:
                title2 = None
                episode = redux_state.get('episode', {})
                if episode.get('id') == group_id:
                    if redux_state:
                        self.report_extraction(group_id, '*_REDUX_STATE__')
                    # try to match the version against the page's version
                    current_version = episode.get('currentVersion')
                    kinds = ['original']
                    if current_version == 'ad':
                        kinds.insert(0, 'audio-described')
                    for kind in kinds:
                        for version in redux_state.get('versions', {}):
                            if try_get(version, lambda x: x['kind'], compat_str) == kind:
                                programme_id = version.get('id')
                                duration = try_get(version, lambda x: x['duration']['seconds'], int)
                                break
                        if programme_id:
                            break
                    title, title2 = get_duple(episode, names=('title', 'subtitle'))
                    if current_version == 'ad':
                        title2 = ([title2] if title2 else []) + ['Audio Described']
                        title2 = ' - '.join(title2)
                    description = self._get_description(episode)
                    thumbnail = try_get(episode, lambda x: x['images']['standard'], compat_str)
                    if programme_id:
                        version = try_get(episode, lambda x: next(y for y in x['versions'] if y['id'] == programme_id), dict) or {}
                        timestamp = unified_timestamp(version.get('firstBroadcast'))
        title = make_title(title, title2)

        if live:
            description = dict_get(episode, ('long_synopsis', 'medium_synopsis', 'short_synopsis'))
            timestamp = parse_iso8601(episode.get('first_broadcast_date'))
            duration = int_or_none(version.get('duration'))
            programme_id = group_id
        else:
            ep_title = title
            tviplayer = self._search_regex(
                r'mediator\.bind\(({.+?})\s*,\s*document\.getElementById',
                webpage, 'player', default=None)

            if tviplayer:
                self.report_extraction(group_id, 'mediator JSON')
                player = self._parse_json(tviplayer, group_id).get('player', {})
                duration = int_or_none(player.get('duration'))
                programme_id = player.get('vpid')

            if not programme_id:
                programme_id = self._search_regex(
                    r'"vpid"\s*:\s*"(%s)"' % self._ID_REGEX, webpage, 'vpid', fatal=False, default=None)

        json_ld_info = self._search_json_ld(webpage, group_id, default={})

        if programme_id:
            formats, subtitles = self._download_media_selector(programme_id)
            title = (
                title
                or self._og_search_title(webpage, default=None)
                or self._html_search_regex(
                    (r'<h2[^>]+id="parent-title"[^>]*>(.+?)</h2>',
                     r'<div[^>]+class="info"[^>]*>\s*<h1>(.+?)</h1>'), webpage, 'title'))
            description = (
                description
                or self._search_regex(
                    (r'<p class="[^"]*medium-description[^"]*">([^<]+)</p>',
                     r'<div[^>]+class="info_+synopsis"[^>]*>([^<]+)</div>'),
                    webpage, 'description', default=None)
                or self._html_search_meta('description', webpage))
        else:
            self.report_extraction(group_id, 'playlist JSON')
            programme_id, title, description, duration, formats, subtitles, thumbnail = self._download_playlist(group_id)
            timestamp = parse_iso8601(self._search_regex(r'"startDate"\s*:\s*"([\w:+.-]+)"', webpage, 'startdate', default=None))

        self._sort_formats(formats)

        if thumbnail:
            thumbnail = thumbnail.format(recipe='raw')
        live = live is not None

        return merge_dicts({
            'id': programme_id,
            'title': self._live_title(title) if live else title,
            'description': description,
            'thumbnail': url_or_none(thumbnail) or self._og_search_thumbnail(webpage, default=None),
            'duration': duration,
            'timestamp': timestamp,
            'formats': formats,
            'subtitles': subtitles,
            'episode': ep_title or title,
            'is_live': live,
        }, json_ld_info)


class BBCIE(BBCBaseIE):
    IE_NAME = 'bbc'
    IE_DESC = 'BBC'
    _VALID_URL = r'https?://(?:www\.)?bbc\.(?:com|co\.uk)/(?:[^/]+/)+(?P<id>[^/#?]+)'

    _TESTS = [{
        # article with multiple videos embedded with data-playable containing vpids
        'url': 'http://www.bbc.com/news/world-europe-32668511',
        'info_dict': {
            'id': 'world-europe-32668511',
            'title': 'Russia stages massive WW2 parade despite Western boycott',
            'description': 'md5:00ff61976f6081841f759a08bf78cc9c',
        },
        'playlist_count': 2,
    }, {
        # article with multiple videos embedded with data-playable (more videos)
        'url': 'http://www.bbc.com/news/business-28299555',
        'info_dict': {
            'id': 'business-28299555',
            'title': 'Farnborough Airshow: Video highlights',
            'description': 'BBC reports and video highlights at the Farnborough Airshow.',
        },
        'playlist_count': 9,
    }, {
        # article with multiple videos embedded with `new SMP()`
        'url': 'http://www.bbc.co.uk/blogs/adamcurtis/entries/3662a707-0af9-3149-963f-47bea720b460',
        'info_dict': {
            'id': '3662a707-0af9-3149-963f-47bea720b460',
            'title': 'BUGGER',
            'description': 'md5:eabb8fb3a4eb831104c70690d1aad082',
        },
        'playlist_count': 18,
    }, {
        # single video embedded with data-playable containing vpid
        'url': 'http://www.bbc.com/news/world-europe-32041533',
        'info_dict': {
            'id': 'p02mprgb',
            'ext': 'mp4',
            'title': 'Germanwings crash site aerial video',
            'description': 'md5:fc2811fe79973d09d83a3d186a379ce7',
            'duration': 47,
            'timestamp': 1427219242,
            'upload_date': '20150324',
        },
        'params': {
            'skip_download': True,
        }
    }, {
        # article with single video (formerly) embedded, now using SIMORGH_DATA JSON
        # TODO: new test needed, or remove tactic
        'url': 'http://www.bbc.com/turkce/haberler/2015/06/150615_telabyad_kentin_cogu',
        'info_dict': {
            'id': '150615_telabyad_kentin_cogu',
            'ext': 'mp4',
            'title': "YPG: Tel Abyad'ın tamamı kontrolümüzde",
            'description': 'md5:33a4805a855c9baf7115fcbde57e7025',
            'timestamp': 1434397334,
            'upload_date': '20150615',
        },
        'skip': 'Video no longer on page',
    }, {
        # single video embedded, legacy media, in promo object of SIMORGH_DATA JSON
        'url': 'http://www.bbc.com/mundo/video_fotos/2015/06/150619_video_honduras_militares_hospitales_corrupcion_aw',
        'info_dict': {
            'id': '39275083',
            'ext': 'mp4',
            'title': 'Honduras militariza sus hospitales por nuevo escándalo de corrupción',
            'description': 'md5:1525f17448c4ee262b64b8f0c9ce66c8',
            'timestamp': 1434713142,
            'upload_date': '20150619',
        },
        'params': {
            'skip_download': True,
        }
    }, {
        # single video from video playlist embedded with vxp-playlist-data JSON
        # TODO: new test needed, or remove tactic
        'url': 'http://www.bbc.com/news/video_and_audio/must_see/33376376',
        'info_dict': {
            'id': 'p02w6qjc',
            'ext': 'mp4',
            'title': '''Judge Mindy Glazer: "I'm sorry to see you here... I always wondered what happened to you"''',
            'duration': 56,
            'description': '''Judge Mindy Glazer: "I'm sorry to see you here... I always wondered what happened to you"''',
        },
        'skip': 'HTTP Error 404: Not Found',
    }, {
        # single video story with digitalData
        # TODO: new test needed, or remove tactic
        'url': 'http://www.bbc.com/travel/story/20150625-sri-lankas-spicy-secret',
        'info_dict': {
            'id': 'p02q6gc4',
            'ext': 'flv',
            'title': 'Sri Lanka’s spicy secret',
            'description': 'As a new train line to Jaffna opens up the country’s north, travellers can experience a truly distinct slice of Tamil culture.',
            'timestamp': 1437674293,
            'upload_date': '20150723',
        },
        'skip': 'Page format changed',
    }, {
        # video article(s) with data in window.__PWA_PRELOADED_STATE__
        'url': 'http://www.bbc.com/travel/story/20150625-sri-lankas-spicy-secret',
        'info_dict': {
            'id': 'p02q6gc4',
            'ext': 'mp4',
            'title': 'Tasting the spice of life in Jaffna',
            'description': 'md5:adccb7473816abc8317149ef713da47b',
            'timestamp': 1437935638,
            'upload_date': '20150726',
        },
    }, {
        # single video story without digitalData
        # TODO: new test needed, or remove tactic
        'url': 'http://www.bbc.com/autos/story/20130513-hyundais-rock-star',
        'info_dict': {
            'id': 'p018zqqg',
            'ext': 'mp4',
            'title': 'Hyundai Santa Fe Sport: Rock star',
            'description': 'md5:b042a26142c4154a6e472933cf20793d',
            'timestamp': 1415867444,
            'upload_date': '20141113',
        },
        'skip': 'Now redirects to topgear.com home page',
    }, {
        # video unavailable, new test
        'url': 'http://www.bbc.co.uk/sport/live/olympics/36895975',
        'only_matching': True,
    }, {
        # single video with playlist.sxml URL in playlist param
        # TODO: new test needed, or remove tactic
        'url': 'http://www.bbc.com/sport/0/football/33653409',
        'info_dict': {
            'id': 'p02xycnp',
            'ext': 'mp4',
            'title': 'Transfers: Cristiano Ronaldo to Man Utd, Arsenal to spend?',
            'description': 'md5:0',
            'duration': 140,
        },
        'skip': 'Page format changed',
    }, {
        # single video in unquoted window.__INITIAL_DATA__
        'url': 'http://www.bbc.com/sport/0/football/33653409',
        'info_dict': {
            'id': 'p02xycnp',
            'ext': 'mp4',
            'title': 'Ronaldo to Man Utd, Arsenal to spend?',
            'description': 'md5:5b7ddf6b532ca8f2af8911c8bf19ebdb',
            'timestamp': 1437750175,
            'upload_date': '20150724',
            'duration': 140,
        },
        'params': {
            # rtmp download
            'skip_download': True,
        }
    }, {
        # article with multiple videos embedded with playlist.sxml in playlist param
        # TODO: new test needed, or remove tactic
        'url': 'http://www.bbc.com/sport/0/football/34475836',
        'info_dict': {
            'id': '34475836',
            'title': 'Jurgen Klopp: Furious football from a witty and winning coach',
            'description': 'Fast-paced football, wit, wisdom and a ready smile - why Liverpool fans should come to love new boss Jurgen Klopp.',
        },
        'playlist_count': 3,
        'skip': 'Page format changed',
    }, {
        # article with multiple videos embedded with Morph
        'url': 'http://www.bbc.com/sport/0/football/34475836',
        'info_dict': {
            'id': '34475836',
            'title': 'What Liverpool can expect from Klopp',
            'description': 'Fast-paced football, wit, wisdom and a ready smile - why Liverpool fans should come to love new boss Jurgen Klopp.',
        },
        'playlist_count': 3,
    }, {
        # data-playable, no vpid, playlist.sxml URLs in otherSettings.playlist
        'url': 'http://www.bbc.com/turkce/multimedya/2015/10/151010_vid_ankara_patlama_ani',
        'info_dict': {
            'id': '40839363',
            'ext': 'mp4',
            'title': 'Ankara\'da patlama anı',
            'description': 'Ankara\'da tren garı önünde meydana gelen patlamaların görüntüleri yayınlandı. Patlamalarda ölü sayısının en az 47 olduğu bildiriliyor.',
            'timestamp': 1444480325,
            'upload_date': '20151010',
        },
    }, {
        # single video embedded, data in playlistObject of playerSettings
        'url': 'https://www.bbc.com/news/av/embed/p07xmg48/50670843',
        'info_dict': {
            'id': 'p07xmg48',
            'ext': 'mp4',
            'title': 'General election 2019: From the count, to your TV',
            'description': 'Behind the scenes of the general election 2019',
            'duration': 160,
        },
    }, {
        # school report article with single video
        'url': 'http://www.bbc.co.uk/schoolreport/35744779',
        'info_dict': {
            'id': '35744779',
            'title': 'School which breaks down barriers in Jerusalem',
        },
        'playlist_count': 1,
        'skip': 'HTTP Error 404: Not Found',

    }, {
        # single video with playlist URL from weather section
        'url': 'http://www.bbc.com/weather/features/33601775',
        'only_matching': True,
    }, {
        # custom redirection to www.bbc.com
        # also, video with window.__INITIAL_DATA__
        'url': 'http://www.bbc.co.uk/news/science-environment-33661876',
        'info_dict': {
            'id': 'p02xzws1',
            'ext': 'mp4',
            'title': "Pluto may have 'nitrogen glaciers'",
            'description': 'md5:6a95b593f528d7a5f2605221bc56912f',
            'thumbnail': r're:https?://.+/.+\.jpg',
            'timestamp': 1437785037,
            'upload_date': '20150725',
        },
    }, {
        # video with window.__INITIAL_DATA__ and value as JSON string
        'url': 'https://www.bbc.com/news/av/world-europe-59468682',
        'info_dict': {
            'id': 'p0b779gc',
            'ext': 'mp4',
            'title': 'Why France is making this woman a national hero',
            'description': 'md5:9ab4f20e2062dc22236bd17e8d0f94a0',
            'thumbnail': r're:https?://.+/.+\.jpg',
            'timestamp': 1638230731,
            'upload_date': '20211130',
        },
    }, {
        # single video article embedded with data-media-vpid
        'url': 'http://www.bbc.co.uk/sport/rowing/35908187',
        'only_matching': True,
    }, {
        # bbcthreeConfig
        'url': 'https://www.bbc.co.uk/bbcthree/clip/73d0bbd0-abc3-4cea-b3c0-cdae21905eb1',
        'info_dict': {
            'id': 'p06556y7',
            'ext': 'mp4',
            'title': 'Things Not To Say to people that live on council estates',
            'description': "From being labelled a 'chav', to the presumption that they're 'scroungers', people who live on council estates encounter all kinds of prejudices and false assumptions about themselves, their families, and their lifestyles. Here, eight people discuss the common statements, misconceptions, and clichés that they're tired of hearing.",
            'duration': 360,
            'thumbnail': r're:https?://.+/.+\.jpg',
        },
    }, {
        # window.__PRELOADED_STATE__
        # TODO: new test needed, or remove tactic
        'url': 'https://www.bbc.co.uk/radio/play/b0b9z4yl',
        'info_dict': {
            'id': 'b0b9z4vz',
            'ext': 'mp4',
            'title': 'Prom 6: An American in Paris and Turangalila',
            'description': 'md5:51cf7d6f5c8553f197e58203bc78dff8',
            'uploader': 'Radio 3',
            'uploader_id': 'bbc_radio_three',
        },
        'skip': 'HTTP Error 404: Not Found',
    }, {
        # data-pid
        'url': 'http://www.bbc.co.uk/learningenglish/chinese/features/lingohack/ep-181227',
        'info_dict': {
            'id': 'p06w9tws',
            'ext': 'mp4',
            'title': 'md5:2fabf12a726603193a2879a055f72514',
            'description': 'Learn English words and phrases from this story',
            'timestamp': 1545350400,
            'upload_date': '20181221',
        },
        'add_ie': [BBCCoUkIE.ie_key()],
    }, {
        # BBC Reel: single video from playlist
        'url': 'https://www.bbc.com/reel/video/p07c6sb6/how-positive-thinking-is-harming-your-happiness',
        'info_dict': {
            'id': 'p07c6sb9',
            'ext': 'mp4',
            'title': 'How positive thinking is harming your happiness',
            'alt_title': 'The downsides of positive thinking',
            'description': 'md5:fad74b31da60d83b8265954ee42d85b4',
            'duration': 235,
            'thumbnail': r're:https?://.+/p07c9dsr.jpg',
            'timestamp': 1559606400,
            'upload_date': '20190604',
            'categories': ['Psychology'],
        },
    }, {
        # BBC Reel: full playlist
        'url': 'https://www.bbc.com/reel/playlist/rethink',
        'info_dict': {
            'id': 'rethink',
            'title': 'ReThink',
        },
        'playlist_count': 9,
    }, {
        # BBC World Service etc: media nested in content object of SIMORGH_DATA JSON
        'url': 'http://www.bbc.co.uk/scotland/articles/cm49v4x1r9lo',
        'info_dict': {
            'id': 'p06p040v',
            'ext': 'mp4',
            'title': 'Five things ants can teach us about management',
            'description': 'They may be tiny, but us humans could learn a thing or two from ants.',
            'timestamp': 1539703557,
            'upload_date': '20181016',
            'duration': 191,
            'thumbnail': r're:https?://.+/p06p0qzv.jpg',
        },
    }, {
        # Morph-based video-block embed
        'url': 'https://www.bbc.co.uk/teach/school-radio/assemblies-the-good-samaritan-modern-setting-ks2/zjsx2v4',
        'info_dict': {
            'id': 'p065hw19',
            'ext': 'mp4',
            'title': 'The Good Samaritan',
            'description': 'The familiar bible story The Good Samaritan is retold in a modern-day setting.',
            'duration': 322,
        },
    }, {
        # Morph-based media-block embed
        'url': 'https://www.bbc.co.uk/newsround/62587298',
        'info_dict': {
            'id': 'p0ctt9ch',
            'ext': 'mp4',
            'title': "'Will we ever know the dinos' true colours?'",
            'description': 'Have you ever wondered what colours the dinosaurs were?And how would we ever find out?That\'s the Big Question sent in by Tom, 12, from OtleyWe asked science educator Tom Luker to explain.Find out more about dino colours here.',
            'timestamp': 1663824151,
            'upload_date': '20220922',
            'duration': 94.0,
        },
    }]

    @classmethod
    def suitable(cls, url):
        EXCLUDE_IE = (BBCCoUkIE, BBCCoUkArticleIE, BBCCoUkIPlayerEpisodesIE, BBCCoUkIPlayerGroupIE, BBCCoUkPlaylistIE)
        return (False if any(ie.suitable(url) for ie in EXCLUDE_IE)
                else super(BBCIE, cls).suitable(url))

    def _extract_from_media_meta(self, media_meta, video_id):
        # Direct links to media in media metadata (eg
        # http://www.bbc.com/turkce/haberler/2015/06/150615_telabyad_kentin_cogu,
        # though no longer in 2022)
        # TODO: there are also f4m and m3u8 streams incorporated in playlist.sxml
        source_files = media_meta.get('sourceFiles')
        if source_files:
            return [{
                'url': f['url'],
                'format_id': format_id,
                'ext': f.get('encoding'),
                'tbr': float_or_none(f.get('bitrate'), 1000),
                'filesize': int_or_none(f.get('filesize')),
            } for format_id, f in source_files.items() if f.get('url')], []

        programme_id = media_meta.get('externalId')
        if programme_id:
            return self._download_media_selector(programme_id)

        # Process playlist.sxml as legacy playlist
        href = media_meta.get('href')
        if href:
            playlist = self._download_legacy_playlist_url(href)
            _, _, _, _, formats, subtitles = self._extract_from_legacy_playlist(playlist, video_id)
            return formats, subtitles

        return [], []

    def _extract_from_playlist_sxml(self, url, playlist_id, timestamp):
        programme_id, title, description, duration, formats, subtitles = \
            self._process_legacy_playlist_url(url, playlist_id)
        self._sort_formats(formats)
        return {
            'id': programme_id,
            'title': title,
            'description': description,
            'duration': duration,
            'timestamp': timestamp,
            'formats': formats,
            'subtitles': subtitles,
        }

    def _extract_from_playlist_object(self, playlist_object):
        title = playlist_object.get('title')
        item_0 = try_get(playlist_object, lambda x: x['items'][0], dict)
        if item_0 and title:
            description = playlist_object.get('summary')
            duration = int_or_none(item_0.get('duration'))
            programme_id = dict_get(item_0, ('vpid', 'versionID'))
            if programme_id:
                return {
                    'id': programme_id,
                    'title': title,
                    'description': description,
                    'duration': duration,
                }
        return {}

    def _get_playlist_entry(self, entry):
        programme_id = entry.get('id')
        if not programme_id:
            return
        formats, subtitles = self._download_media_selector(programme_id)
        self._sort_formats(formats)
        entry.update({
            'formats': formats,
            'subtitles': subtitles,
        })
        return entry

    def _page_error(self, webpage):
        return None

    def _real_extract(self, url):
        playlist_id = self._match_id(url)

        webpage = self._download_video_page(url, playlist_id)

        json_ld_info = self._search_json_ld(webpage, playlist_id, default={})
        timestamp = json_ld_info.get('timestamp')

        playlist_title = json_ld_info.get('title')
        if not playlist_title:
            playlist_title = self._og_search_title(
                webpage, default=None) or self._html_search_regex(
                r'<title>(.+?)</title>', webpage, 'playlist title', default=None)
            if playlist_title:
                playlist_title = re.sub(r'(.+)\s*-\s*BBC(?:\s+\w+)?$', r'\1', playlist_title).strip()

        playlist_description = json_ld_info.get(
            'description') or self._og_search_description(webpage, default=None)

        if not timestamp:
            timestamp = parse_iso8601(self._search_regex(
                [r'<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"',
                 r'itemprop="datePublished"[^>]+datetime="([^"]+)"',
                 r'"datePublished":\s*"([^"]+)'],
                webpage, 'date', default=None))
        duration = float_or_none(json_ld_info.get('duration'))

        entries = []

        # article with multiple videos embedded with playlist.sxml (eg
        # formerly http://www.bbc.com/sport/0/football/34475836)
        playlists = re.findall(r'<param[^>]+name="playlist"[^>]+value="([^"]+)"', webpage)
        playlists.extend(re.findall(r'data-media-id="([^"]+/playlist\.sxml)"', webpage))
        if playlists:
            self.report_extraction(playlist_id, 'playlist values and playlist.sxml')
            entries = (
                self._extract_from_playlist_sxml(playlist_url, playlist_id, timestamp)
                for playlist_url in playlists)
            if entries:
                return self.playlist_result(entries, playlist_id, playlist_title, playlist_description)

        # news article with multiple videos embedded with data-playable
        data_playables = re.findall(r'data-playable=(["\'])({.+?})\1', webpage)
        if data_playables:
            for _, data_playable_json in data_playables:
                data_playable = self._parse_json(
                    unescapeHTML(data_playable_json), playlist_id, fatal=False)
                if not data_playable:
                    continue
                settings = data_playable.get('settings', {})
                if settings:
                    # data-playable with video vpid in settings.playlistObject.items
                    # obsolete? example previously quoted uses __INITIAL_DATA__ now
                    playlist_object = settings.get('playlistObject', {})
                    if playlist_object:
                        self.report_extraction(playlist_id, 'settings.playlistObject')
                        entry = self._extract_from_playlist_object(playlist_object)
                        entry = self._get_playlist_entry(entry)
                        if entry:
                            entry.update({
                                'timestamp': timestamp,
                            })
                            entries.append(entry)
                    else:
                        # data-playable without vpid but with a playlist.sxml URLs
                        # in otherSettings.playlist (eg
                        # http://www.bbc.com/turkce/multimedya/2015/10/151010_vid_ankara_patlama_ani)
                        playlist = data_playable.get('otherSettings', {}).get('playlist', {})
                        if playlist:
                            self.report_extraction(playlist_id, 'data-playable with a playlist.sxml')
                            entry = None
                            for key in ('streaming', 'progressiveDownload'):
                                playlist_url = playlist.get('%sUrl' % key)
                                if not playlist_url:
                                    continue
                                try:
                                    info = self._extract_from_playlist_sxml(
                                        playlist_url, playlist_id, timestamp)
                                    if not entry:
                                        entry = info
                                    else:
                                        entry['title'] = info['title']
                                        entry['formats'].extend(info['formats'])
                                except ExtractorError as e:
                                    # Some playlist URL may fail with 500, at the same time
                                    # the other one may work fine (eg
                                    # http://www.bbc.com/turkce/haberler/2015/06/150615_telabyad_kentin_cogu)
                                    if isinstance(e.cause, compat_HTTPError) and e.cause.code == 500:
                                        continue
                                    raise
                            if entry:
                                self._sort_formats(entry['formats'])
                                entries.append(entry)
        else:
            # embed video with playerSettings, eg
            # https://www.bbc.com/news/av/embed/p07xmg48/50670843
            settings = self._html_search_regex(
                r'(?s)<script\b[^>]+>.+\.playerSettings\s*=\s*(?P<json>\{.*\})\s*(?:,\s*function\s*\(\s*\)\s*\{\s*["\']use strict.+\(\s*\)\s*)?</script\b',
                webpage, 'player settings', default='{}', group='json')
            settings = self._parse_json(settings, playlist_id, transform_source=js_to_json, fatal=False)
            if settings:
                playlist_object = settings.get('playlistObject', {})
                if playlist_object:
                    self.report_extraction(playlist_id, 'playlistObject in playerSettings')
                    entry = self._extract_from_playlist_object(playlist_object)
                    entry = self._get_playlist_entry(entry)
                    if entry:
                        thumbnail = playlist_object.get('holdingImageURL')
                        entry.update({
                            'timestamp': timestamp,
                            'thumbnail': thumbnail.replace('$recipe', 'raw') if thumbnail else None,
                        })
                        entries.append(entry)
        if entries:
            return self.playlist_result(entries, playlist_id, playlist_title, playlist_description)

        # http://www.bbc.co.uk/learningenglish/chinese/features/lingohack/ep-181227
        group_id = self._search_regex(
            r'<div[^>]+\bclass=["\']video["\'][^>]+\bdata-pid=["\'](%s)' % self._ID_REGEX,
            webpage, 'group id', default=None)
        if group_id:
            self.report_extraction(playlist_id, 'data-pid')
            return self.url_result(
                'https://www.bbc.co.uk/programmes/%s' % group_id,
                ie=BBCCoUkIE.ie_key())

        # single video story (eg formerly http://www.bbc.com/travel/story/20150625-sri-lankas-spicy-secret)
        programme_id = self._search_regex(
            [r'data-(?:video-player|media)-vpid="(%s)"' % self._ID_REGEX,
             r'<param[^>]+name="externalIdentifier"[^>]+value="(%s)"' % self._ID_REGEX,
             r'videoId\s*:\s*["\'](%s)["\']' % self._ID_REGEX],
            webpage, 'vpid', default=None)

        if programme_id:
            self.report_extraction(playlist_id, 'data-...-vpid')
            formats, subtitles = self._download_media_selector(programme_id)
            self._sort_formats(formats)
            # digitalData may be missing (eg http://www.bbc.com/autos/story/20130513-hyundais-rock-star)
            digital_data = self._parse_json(
                self._search_regex(
                    r'(?s)var\s+digitalData\s*=\s*({.+?});?\n', webpage, 'digital data', default='{}'),
                programme_id, fatal=False)
            page_info = digital_data.get('page', {}).get('pageInfo', {})
            title = page_info.get('pageName') or self._og_search_title(webpage)
            description = page_info.get('description') or self._og_search_description(webpage)
            timestamp = parse_iso8601(page_info.get('publicationDate')) or timestamp
            return {
                'id': programme_id,
                'title': title,
                'description': description,
                'timestamp': timestamp,
                'formats': formats,
                'subtitles': subtitles,
            }

        # video article(s) with data in window.__PWA_PRELOADED_STATE__ (eg
        # http://www.bbc.com/travel/story/20150625-sri-lankas-spicy-secret)
        preload_state = self._parse_json(
            self._search_regex(
                r'(?s)window\.__PWA_PRELOADED_STATE__\s*=\s*({.+?})\s*</script', webpage,
                'pwa preload state', default='{}'),
            playlist_id, transform_source=js_to_json, fatal=False)
        if preload_state:
            self.report_extraction(playlist_id, '__PWA_PRELOADED_STATE__')
            path = try_get(preload_state, lambda x: x['router']['location']['pathname'].lstrip('/'), compat_str)
            video_ids = path and try_get(preload_state, lambda x: x['entities']['articles'][path]['assetVideo'], list)
            all_video_data = try_get(preload_state, lambda x: x['entities']['videos'], dict) or {}
            for video_id in video_ids:
                if not re.match(self._ID_REGEX, video_id):
                    continue
                video_data = all_video_data.get(video_id, {})
                entry = self.url_result(
                    self._URL_TEMPLATE % (video_id, ),
                    video_id=video_id, video_title=video_data.get('title'))
                entry.update({
                    '_type': 'url_transparent',
                    'description': self._get_description({self._DESCRIPTION_KEY: video_data}, ('synopsisLong', 'synopsisMedium', 'synopsisShort')),
                    'duration': int_or_none(video_data.get('duration')),
                    'timestamp': parse_iso8601(try_get(preload_state, lambda x: x['entities']['articles'][path]['displayDate'], compat_str)),
                })
                entries.append(entry)
            if entries:
                return self.playlist_result(entries, playlist_id, playlist_title, playlist_description)

        # bbc reel (eg https://www.bbc.com/reel/video/p07c6sb6/how-positive-thinking-is-harming-your-happiness)
        initial_data = self._parse_json(self._html_search_regex(
            r'(?s)<script[^>]+id=(["\'])initial-data\1[^>]+data-json=(["\'])(?P<json>(?:(?!\2).)+)',
            webpage, 'initial data', default='{}', group='json'), playlist_id, fatal=False)
        if initial_data:
            self.report_extraction(playlist_id, 'initial-data in data-json')
            init_data = try_get(initial_data, lambda x: x['initData'], dict) or {}
            items = try_get(init_data, lambda x: x['items'], list)
            if items is None:
                items = [init_data] if init_data else []
            for item in items:
                smp_data = item.get('smpData') or {}
                title = smp_data.get('title') or init_data.get('shortTitle')
                programme_id = item.get('clipPID')
                clip_data = try_get(smp_data, lambda x: x['items'][0], dict) or {}
                version_id = clip_data.get('versionID')
                if programme_id:
                    entry = self.url_result(
                        self._URL_TEMPLATE % (programme_id, ),
                        video_id=programme_id, video_title=title)
                else:
                    if not version_id and title:
                        continue
                    formats, subtitles = self._download_media_selector(version_id)
                    if not formats:
                        continue
                    self._sort_formats(formats)
                    entry = {
                        'id': version_id,
                        'title': title,
                        'formats': formats,
                        'subtitles': subtitles,
                    }
                image_url = smp_data.get('holdingImageURL')
                display_date = item.get('displayDate')
                topic_title = item.get('topicTitle')
                entry.update({
                    '_type': 'url_transparent',
                    'alt_title': item.get('shortTitle'),
                    'thumbnail': image_url.replace('$recipe', 'raw') if image_url else None,
                    'description': smp_data.get('summary') or item.get('shortSummary'),
                    'upload_date': display_date.replace('-', '') if display_date else None,
                    'duration': int_or_none(clip_data.get('duration')) or duration,
                    'categories': [topic_title] if topic_title else None,
                })
                # if URL contains eg /id/ or ?vpid=id, return that item only
                if (programme_id or version_id) and re.search(r'\b(?:%s)\b' % ('|'.join((re.escape(programme_id), re.escape(version_id))), ), url):
                    return entry
                entries.append(entry)
            if entries:
                return self.playlist_result(entries, playlist_id, playlist_title, playlist_description)

        def extract_all_json(pattern):
            return list(filter(None, map(
                lambda s: self._parse_json(s, playlist_id, fatal=False),
                re.findall(pattern, webpage))))

        # Morph-based embed (eg http://www.bbc.com/sport/0/football/34475836)
        # Several setPayload calls may be present: the video is not
        # always found in the first one
        morph_payload = None
        for morph_payload in extract_all_json(r'(?s)Morph\.setPayload\([^,]+,\s*({.+?})\);'):
            if not isinstance(morph_payload, dict):
                continue
            statuses = try_get(morph_payload, lambda x: next(y for y in x['body']['components'] if y['id'] == 'lx-event-summary')['props']['statuses'], dict) or {}
            if statuses and all(statuses.get(x) == 'NONE' for x in ('audio', 'video')):
                self._raise_extractor_error('Media no longer available')
            video = try_get(morph_payload, lambda x: x['body']['video'], dict)
            if video:
                # Morph video-block
                self.report_extraction(playlist_id, 'Morph video payload')
                programme_id = morph_payload['body'].get('pid')
                entry = self.url_result(
                    self._URL_TEMPLATE % (programme_id, ),
                    video_id=programme_id, video_title=video.get('title') or self._og_search_title(webpage))
                entry.update({
                    '_type': 'url_transparent',
                    'description': video.get('summary') or video.get('caption'),
                    'duration': parse_duration(video.get('duration')),
                })
                return entry
            video = try_get(morph_payload, lambda x: x['body']['media'], dict) or {}
            if video:
                # Morph media-block
                programme_id = video.get('pid')
                if not programme_id:
                    programme_id = try_get(video, lambda x: next(iter(x['videos']['primary'].values()))['externalId'], compat_str)
                if programme_id:
                    self.report_extraction(playlist_id, 'Morph media payload')
                    entry = self.url_result(
                        self._URL_TEMPLATE % (programme_id, ),
                        video_id=programme_id, video_title=morph_payload['body'].get('title'))
                    entry.update({
                        '_type': 'url_transparent',
                        'description': clean_html(morph_payload['body'].get('body')),
                        'duration': parse_duration(video.get('duration')),
                        'timestamp': parse_iso8601(morph_payload['body'].get('dateTime')),
                    })
                    return entry

            # Morph article
            article = try_get(morph_payload, (lambda x: x['body']['content']['article'],), ) or {}
            if not article:
                continue
            body = self._parse_json(article.get('body') or '{}', playlist_id)
            if not body:
                continue
            self.report_extraction(playlist_id, 'Morph article payload')
            for component in body:
                if component.get('name') != 'video':
                    continue
                video_data = component.get('videoData')
                if not video_data:
                    continue
                programme_id = video_data.get('vpid')
                formats, subtitles = self._download_media_selector(programme_id)
                if not formats:
                    continue
                self._sort_formats(formats)
                entries.append({
                    'id': programme_id,
                    'title': video_data.get('title') or self._og_search_title(webpage),
                    'description': video_data.get('summary') or video_data.get('caption'),
                    'duration': parse_duration(video_data.get('duration')),
                    'formats': formats,
                    'subtitles': subtitles,
                })
            if entries:
                return self.playlist_result(entries, playlist_id, playlist_title, playlist_description)
        else:
            if morph_payload:
                self._raise_extractor_error('No video found in Morph-based page')

        # simorgh-based playlist (see https://github.com/bbc/simorgh)
        # JSON assigned to window.SIMORGH_DATA in a <script> element
        simorgh_data = self._parse_json(
            self._search_regex(
                r'window\.SIMORGH_DATA\s*=\s*(\{[^<]+})\s*</',
                webpage, 'simorgh playlist', default='{}'),
            playlist_id, fatal=False)
        # legacy media, video in promo object (eg, http://www.bbc.com/mundo/video_fotos/2015/06/150619_video_honduras_militares_hospitales_corrupcion_aw)
        playlist = try_get(simorgh_data, lambda x: x['pageData']['promo']['media']['playlist']) or []
        if playlist:
            self.report_extraction(playlist_id, 'promo in SIMORGH_DATA')
            media = simorgh_data['pageData']['promo']
            if media['media'].get('format') == 'video':
                media.update(media['media'])
                title = (
                    dict_get(media.get('headlines') or {},
                             ('shortHeadline', 'headline'))
                    or playlist_title)
                programme_id = media.get('id')
                if programme_id and title:
                    formats = []
                    keys = ('url', 'format', 'format_id', 'language', 'quality', 'tbr', 'resolution')
                    for format in playlist:
                        if not (format.get('url') and format.get('format')):
                            continue
                        bitrate = format.pop('bitrate')
                        format['tbr'] = int_or_none(bitrate, scale=1000) or parse_bitrate(bitrate)
                        format['language'] = media.get('language')
                        # format id: penultimate item from the url split on _ and .
                        (fmt,) = re.split('[_.]', format['url'])[-2:][:1]
                        format['format_id'] = '%s_%s' % (format['format'], fmt)
                        # try to set resolution using any available data
                        aspect_ratio = re.split(r'[xX:]', media.get('aspectRatio') or '')
                        aspect_ratio = float_or_none(aspect_ratio[0], scale=aspect_ratio[1]) if len(aspect_ratio) == 2 else None
                        # these may not be present, but try anyway
                        width = int_or_none(format.get('width'))
                        height = int_or_none(format.get('height'))
                        if aspect_ratio:
                            if not width:
                                width = int_or_none(height, invscale=aspect_ratio)
                            elif not height:
                                height = int_or_none(width, scale=aspect_ratio)
                        format['resolution'] = ('%dx%d' % (width, height) if width and height
                                                else dict_get(format, ('resolution', 'res'), default=fmt))
                        format['quality'] = -1
                        formats.append(dict((k, format[k]) for k in keys))
                    self._sort_formats(formats)
                    return {
                        'id': programme_id,
                        'title': title,
                        'description': media.get('summary') or playlist_description,
                        'formats': formats,
                        'subtitles': None,
                        'thumbnail': url_or_none(try_get(media, lambda x: x['image']['href'])),
                        'timestamp': int_or_none(media.get('timestamp'), scale=1000)
                    }

        # general case: media nested in content object
        # test: https://www.bbc.co.uk/scotland/articles/cm49v4x1r9lo
        if simorgh_data:
            self.report_extraction(playlist_id, 'media nested in SIMORGH_DATA')

            def extract_media_from_simorgh(model):
                if not isinstance(model, dict):
                    return
                for block in model.get('blocks') or {}:
                    if block.get('type') == 'aresMediaMetadata':
                        vpid = try_get(block, lambda x: x['model']['versions'][0]['versionId'])
                        if vpid:
                            formats, subtitles = self._download_media_selector(vpid)
                            self._sort_formats(formats)
                            model = block['model']
                            version = model['versions'][0]
                            thumbnail = model.get('imageUrl')
                            return {
                                'id': vpid,
                                'title': model.get('title') or 'unnamed clip',
                                'description': self._get_description(model, ('long', 'medium', 'short')),
                                'duration': (int_or_none(version.get('duration'))
                                             or parse_duration(version.get('durationISO8601'))),
                                'timestamp': int_or_none(version.get('availableFrom'), 1000),
                                'thumbnail': urljoin(url, thumbnail.replace('$recipe', 'raw')) if thumbnail else None,
                                'formats': formats,
                                'subtitles': subtitles,
                            }
                    else:
                        entry = extract_media_from_simorgh(block.get('model'))
                        if entry:
                            return entry

            playlist = extract_media_from_simorgh(try_get(simorgh_data, lambda x: x['pageData']['content']['model'], dict))
            if playlist:
                return playlist

        preload_state = self._preload_state(webpage, playlist_id)
        if preload_state:
            self.report_extraction(playlist_id, '__PRELOADED_STATE__')
            current_programme = preload_state.get('programmes', {}).get('current') or {}
            programme_id = current_programme.get('id')
            if current_programme and programme_id and current_programme.get('type') == 'playable_item':
                title = current_programme.get('titles', {}).get('tertiary') or playlist_title
                formats, subtitles = self._download_media_selector(programme_id)
                self._sort_formats(formats)
                synopses = current_programme.get('synopses') or {}
                network = current_programme.get('network') or {}
                duration = int_or_none(
                    current_programme.get('duration', {}).get('value'))
                thumbnail = None
                image_url = current_programme.get('image_url')
                if image_url:
                    thumbnail = image_url.replace('{recipe}', 'raw')
                return {
                    'id': programme_id,
                    'title': title,
                    'description': dict_get(synopses, ('long', 'medium', 'short')),
                    'thumbnail': thumbnail,
                    'duration': duration,
                    'uploader': network.get('short_title'),
                    'uploader_id': network.get('id'),
                    'formats': formats,
                    'subtitles': subtitles,
                }

        bbc3_config = self._parse_json(
            self._search_regex(
                r'(?s)bbcthreeConfig\s*=\s*({.+?})\s*;\s*<', webpage,
                'bbcthree config', default='{}'),
            playlist_id, transform_source=js_to_json, fatal=False) or {}
        payload = bbc3_config.get('payload') or {}
        if payload:
            self.report_extraction(playlist_id, 'bbcthreeConfig')
            clip = payload.get('currentClip') or {}
            clip_vpid = clip.get('vpid')
            clip_title = clip.get('title')
            if clip_vpid and clip_title:
                formats, subtitles = self._download_media_selector(clip_vpid)
                self._sort_formats(formats)
                return {
                    'id': clip_vpid,
                    'title': clip_title,
                    'thumbnail': dict_get(clip, ('poster', 'imageUrl')),
                    'description': clip.get('description'),
                    'duration': parse_duration(clip.get('duration')),
                    'formats': formats,
                    'subtitles': subtitles,
                }
            bbc3_playlist = try_get(
                payload, lambda x: x['content']['bbcMedia']['playlist'],
                dict)
            if bbc3_playlist:
                playlist_title = bbc3_playlist.get('title') or playlist_title
                thumbnail = bbc3_playlist.get('holdingImageURL')
                entries = []
                for bbc3_item in bbc3_playlist['items']:
                    programme_id = bbc3_item.get('versionID')
                    if not programme_id:
                        continue
                    formats, subtitles = self._download_media_selector(programme_id)
                    self._sort_formats(formats)
                    entries.append({
                        'id': programme_id,
                        'title': playlist_title,
                        'thumbnail': thumbnail,
                        'timestamp': timestamp,
                        'formats': formats,
                        'subtitles': subtitles,
                    })
                return self.playlist_result(
                    entries, playlist_id, playlist_title, playlist_description)

        initial_data = self._search_regex(
            r'(?s)window\.__INITIAL_DATA__\s*=\s*("{.+?}")\s*;', webpage,
            'quoted preload state', default=None)
        if initial_data is None:
            initial_data = self._search_regex(
                r'(?s)window\.__INITIAL_DATA__\s*=\s*({.+?})\s*;', webpage,
                'preload state', default='{}')
        else:
            initial_data = self._parse_json(initial_data or '"{}"', playlist_id, fatal=False)
        initial_data = self._parse_json(initial_data, playlist_id, fatal=False)
        if initial_data:
            self.report_extraction(playlist_id, '__INITIAL_DATA__')

            def parse_media(media):
                if not media:
                    return
                for item in (try_get(media, lambda x: x['media']['items'], list) or []):
                    item_id = item.get('id')
                    item_title = item.get('title')
                    if not (item_id and item_title):
                        continue
                    formats, subtitles = self._download_media_selector(item_id)
                    if not formats:
                        continue
                    self._sort_formats(formats)
                    item_duration = int_or_none(item.get('duration'))
                    item_desc = None
                    blocks = try_get(media, lambda x: x['summary']['blocks'], list)
                    if blocks:
                        summary = []
                        for block in blocks:
                            text = try_get(block, lambda x: x['model']['text'], compat_str)
                            if text:
                                summary.append(text)
                        if summary:
                            item_desc = '\n\n'.join(summary)
                    item_time = None
                    for meta in try_get(media, lambda x: x['metadata']['items'], list) or []:
                        if try_get(meta, lambda x: x['label']) == 'Published':
                            item_time = unified_timestamp(meta.get('timestamp'))
                            break
                    entries.append({
                        'id': item_id,
                        'title': item_title,
                        'thumbnail': item.get('holdingImageUrl'),
                        'formats': formats,
                        'subtitles': subtitles,
                        'timestamp': item_time,
                        'duration': duration if item_duration is None else item_duration,
                        'description': strip_or_none(item_desc) or None,
                    })

            for resp in (initial_data.get('data') or {}).values():
                name = resp.get('name')
                if name == 'media-experience':
                    parse_media(try_get(resp, lambda x: x['data']['initialItem']['mediaItem'], dict))
                elif name == 'article':
                    for block in (try_get(resp,
                                          (lambda x: x['data']['blocks'],
                                           lambda x: x['data']['content']['model']['blocks'],),
                                          list) or []):
                        if block.get('type') not in ['media', 'video']:
                            continue
                        parse_media(block.get('model'))
            return self.playlist_result(
                entries, playlist_id, playlist_title, playlist_description)

        # Multiple video article (eg
        # http://www.bbc.co.uk/blogs/adamcurtis/entries/3662a707-0af9-3149-963f-47bea720b460)
        EMBED_URL = r'https?://(?:www\.)?bbc\.co\.uk/(?:[^/]+/)+%s(?:\b[^"]+)?' % self._ID_REGEX
        entries = []
        for match in extract_all_json(r'new\s+SMP\(({.+?})\)'):
            embed_url = match.get('playerSettings', {}).get('externalEmbedUrl')
            if embed_url and re.match(EMBED_URL, embed_url):
                entries.append(embed_url)
        entries.extend(re.findall(
            r'setPlaylist\("(%s)"\)' % EMBED_URL, webpage))
        if entries:
            self.report_extraction(playlist_id, 'SMP/setPlaylist')
            return self.playlist_result(
                [self.url_result(entry_, 'BBCCoUk') for entry_ in entries],
                playlist_id, playlist_title, playlist_description)

        # Multiple video article (eg http://www.bbc.com/news/world-europe-32668511)
        medias = extract_all_json(r"data-media-meta='({[^']+})'")

        if medias:
            self.report_extraction(playlist_id, 'data-media-meta')
        else:
            # Single video article (eg http://www.bbc.com/news/video_and_audio/international)
            media_asset = self._search_regex(
                r'mediaAssetPage\.init\(\s*({.+?}), "/',
                webpage, 'media asset', default=None)
            if media_asset:
                self.report_extraction(playlist_id, 'mediaAssetPage.init')
                media_asset_page = self._parse_json(media_asset, playlist_id, fatal=False)
                medias = []
                for video in media_asset_page.get('videos', {}).values():
                    medias.extend(video.values())

        if not medias:
            # Multiple video playlist with single `now playing` entry (eg
            # http://www.bbc.com/news/video_and_audio/must_see/33767813)
            vxp_playlist = self._parse_json(
                self._search_regex(
                    r'<script[^>]+class="vxp-playlist-data"[^>]+type="application/json"[^>]*>([^<]+)</script>',
                    webpage, 'playlist data'),
                playlist_id)
            playlist_medias = []
            if vxp_playlist:
                self.report_extraction(playlist_id, 'vxp-playlist-data')
            for item in vxp_playlist:
                media = item.get('media')
                if not media:
                    continue
                playlist_medias.append(media)
                # Download single video if found media with asset id matching the video id from URL
                if item.get('advert', {}).get('assetId') == playlist_id:
                    medias = [media]
                    break
            # Fallback to the whole playlist
            if not medias:
                medias = playlist_medias

        entries = []
        for num, media_meta in enumerate(medias, start=1):
            formats, subtitles = self._extract_from_media_meta(media_meta, playlist_id)
            if not formats:
                continue
            self._sort_formats(formats)

            video_id = media_meta.get('externalId')
            if not video_id:
                video_id = playlist_id if len(medias) == 1 else '%s-%s' % (playlist_id, num)

            title = media_meta.get('caption')
            if not title:
                title = playlist_title if len(medias) == 1 else '%s - Video %s' % (playlist_title, num)

            duration = int_or_none(media_meta.get('durationInSeconds')) or parse_duration(media_meta.get('duration'))

            images = []
            for image in media_meta.get('images', {}).values():
                images.extend(image.values())
            if 'image' in media_meta:
                images.append(media_meta['image'])

            thumbnails = [{
                'url': image.get('href'),
                'width': int_or_none(image.get('width')),
                'height': int_or_none(image.get('height')),
            } for image in images]

            entries.append({
                'id': video_id,
                'title': title,
                'thumbnails': thumbnails,
                'duration': duration,
                'timestamp': timestamp,
                'formats': formats,
                'subtitles': subtitles,
            })

        return self.playlist_result(entries, playlist_id, playlist_title, playlist_description)


class BBCCoUkPlaylistIE(BBCBaseIE):
    IE_NAME = 'bbc.co.uk:playlist'
    _VALID_URL = r'https?://(?:www\.)?bbc\.co\.uk/programmes/(?P<id>%s)/(?:episodes|broadcasts|clips)' % (BBCBaseIE._ID_REGEX, )
    _VIDEO_ID_TEMPLATE = r'data-pid=["\'](?P<id>%s)'
    _TESTS = [{
        'url': 'http://www.bbc.co.uk/programmes/b05rcz9v/clips',
        'info_dict': {
            'id': 'b05rcz9v',
            'title': 're:' + _with_ident_re('The Disappearance - Clips'),
            'description': 'Clips from The Disappearance',
        },
        'playlist_mincount': 0,
    }, {
        'url': 'https://www.bbc.co.uk/programmes/p09pm77q/clips',
        'info_dict': {
            'id': 'p09pm77q',
            'title': 're:' + _with_ident_re('Vigil - Clips'),
            'description': 'Clips from Vigil',
        },
        'playlist_mincount': 3,
    }, {
        'note': 'multipage playlist, explicit page',
        'url': 'http://www.bbc.co.uk/programmes/b00mfl7n/clips?page=1',
        'info_dict': {
            'id': 'b00mfl7n',
            'title': 're:' + _with_ident_re('Frozen Planet - Clips'),
            'description': 'Clips from Frozen Planet',
        },
        'playlist_count': 24,
    }, {
        'note': 'multipage playlist, all pages',
        'url': 'http://www.bbc.co.uk/programmes/b00mfl7n/clips',
        'info_dict': {
            'id': 'b00mfl7n',
            'title': 're:' + _with_ident_re('Frozen Planet - Clips'),
            'description': 'Clips from Frozen Planet',
        },
        'playlist_mincount': 142,
    }, {
        'url': 'http://www.bbc.co.uk/programmes/b05rcz9v/broadcasts/2016/06',
        'only_matching': True,
    }, {
        'url': 'http://www.bbc.co.uk/programmes/b05rcz9v/clips',
        'only_matching': True,
    }, {
        'url': 'http://www.bbc.co.uk/programmes/b055jkys/episodes/player',
        'only_matching': True,
    }, {
        'note': 'explicit page',
        'url': 'https://www.bbc.co.uk/programmes/m0004c4v/episodes/player?page=2',
        'info_dict': {
            'id': 'm0004c4v',
            'title': 're:' + _with_ident_re('Beechgrove - Available now'),
            'description': 'Available episodes of Beechgrove',
        },
        'playlist_count': 10,
    }, {
        'note': 'all pages',
        'url': 'https://www.bbc.co.uk/programmes/m0004c4v/episodes/player',
        'info_dict': {
            'id': 'm0004c4v',
            'title': 're:' + _with_ident_re('Beechgrove - Available now'),
            'description': 'Available episodes of Beechgrove',
        },
        'playlist_mincount': 35,
    }]

    def _entries(self, url, playlist_id, webpage=None):
        single_page = 'page' in compat_urlparse.parse_qs(
            compat_urlparse.urlparse(url).query)
        next_page = None
        for page_num in itertools.count(1):
            if not webpage:
                if page_num == 1:
                    webpage = self._download_webpage(url, playlist_id)
                else:
                    webpage = self._download_webpage(
                        compat_urlparse.urljoin(url, next_page), playlist_id,
                        'Downloading page %d' % page_num, page_num)
            for m in re.finditer(
                    self._VIDEO_ID_TEMPLATE % (self._ID_REGEX, ), webpage):
                yield self.url_result(
                    self._URL_TEMPLATE % (m.group('id'), ), BBCCoUkIE.ie_key())
            if single_page:
                return
            next_page = self._search_regex(
                r'<li\b[^>]+class\s*=\s*(["\'])pagination_+next\1[^>]*>\s*<a\b[^>]+\bhref\s*=\s*(["\'])(?P<url>(?:(?!\2).)+)\2',
                webpage, 'next page url', default=None, group='url')
            if not next_page:
                break
            webpage = None

    def _extract_title_and_description(self, webpage, data):
        title = self._og_search_title(webpage, fatal=False)
        description = self._og_search_description(webpage)
        return title, description

    def _get_playlist_data(self, playlist_id, url):
        return self._download_webpage(url, playlist_id), None

    def _real_extract(self, url):
        playlist_id = self._match_id(url)

        webpage, playlist_data = self._get_playlist_data(playlist_id, url)

        title, description = self._extract_title_and_description(webpage, playlist_data)

        return self.playlist_result(
            self._entries(url, playlist_id, webpage),
            playlist_id, title, description)


class BBCCoUkArticleIE(BBCCoUkPlaylistIE):
    _VALID_URL = r'https?://(?:www\.)?bbc\.co\.uk/programmes/articles/(?P<id>[a-zA-Z0-9]+)'
    IE_NAME = 'bbc.co.uk:article'
    IE_DESC = 'BBC articles'

    _TESTS = [{
        'url': 'http://www.bbc.co.uk/programmes/articles/3jNQLTMrPlYGTBn0WV6M2MS/not-your-typical-role-model-ada-lovelace-the-19th-century-programmer',
        'info_dict': {
            'id': '3jNQLTMrPlYGTBn0WV6M2MS',
            'title': r're:(?:BBC\s+\w+\s+-\s+)?Calculating Ada: The Countess of Computing - Not your typical role model: Ada Lovelace the 19th century programmer(?:\s+-\s+BBC\s+\w+)?',
            'description': 'Hannah Fry reveals some of her surprising discoveries about Ada Lovelace during filming.',
        },
        'playlist_count': 4,
        'add_ie': ['BBCCoUk'],
    },
    ]

    def _entries(self, url, playlist_id, webpage=None):
        for m in re.finditer(
                r'<div\b[^>]+\bclass\s*=\s*(["\'])(?=.*\bprogramme--clip\b)(?:(?!\1).)+\1[^>]*>', webpage):
            attrs = extract_attributes(m.group(0))
            pid = attrs.get('data-pid')
            if pid:
                yield self.url_result(self._URL_TEMPLATE % (pid, ))
        # probably no longer sent?
        for m in re.finditer(
                r'<div\b[^>]+\btypeof\s*=\s*(["\'])Clip\1[^>]+\bresource\s*=\s*(["\'])(?P<url>(?:(?!\2).)+)\2', webpage):
            yield self.url_result(m.group('url'))


class BBCCoUkIPlayerPlaylistBaseIE(BBCCoUkPlaylistIE):
    _VALID_URL_TMPL = r'https?://(?:www\.)?bbc\.co\.uk/iplayer/%%s/(?P<id>%s)' % BBCBaseIE._ID_REGEX

    @staticmethod
    def _get_default(episode, key, default_key='default'):
        return try_get(episode, lambda x: x[key][default_key])

    @staticmethod
    def _extract_playlist_data(data):
        return data

    def _get_playlist_data(self, playlist_id, url):
        data = self._extract_playlist_data(self._call_api(playlist_id, 1))
        if data is None:
            self._raise_extractor_error('No episodes available')
        season_id = compat_urlparse.parse_qs(compat_urlparse.urlparse(url).query).get('seriesId', [None])[-1]
        if season_id:
            title = self._get_playlist_title(data)
            season = next((self._get_default(s, 'title') for s in data.get('slices') if s.get('id') == season_id), None)
            if title and season:
                data['title']['default'] = '%s (%s)' % (title, season)
        return None, data

    def _get_playlist_title(self, data):
        return self._get_default(data, 'title')

    def _extract_title_and_description(self, webpage, data):
        return self._get_playlist_title(data), self._get_description(data)

    def _entries(self, url, playlist_id, webpage):
        query = compat_urlparse.parse_qs(compat_urlparse.urlparse(url).query)

        single_season, single_page = (k in query for k in ('seriesId', 'page'))

        if not webpage:
            webpage = self._download_webpage(url, playlist_id)

        redux_state = self._redux_state(webpage, playlist_id)
        slices = redux_state.get('header', {}).get('availableSlices') or []
        # seasons = list(map(lambda s: s.get('id'), slices))
        if not slices:
            slices = [{'id': None, 'title': None}]
            single_season = True

        season = slices[0].get('title')

        for season_n in range(1, len(slices) + 1):
            page_num = total_pages = 1
            season_txt = ('%s ' % (season, )) if season else ('S%0d' % (season_n, )) if season_n > 1 else ''
            while page_num <= total_pages:
                if not webpage:
                    what = '%spage %d' % (season_txt, page_num, )
                    webpage = self._download_webpage(
                        url, playlist_id,
                        'Downloading ' + what, 'Failed to download ' + what)
                if not redux_state:
                    redux_state = self._redux_state(webpage, playlist_id)

                pagination = redux_state.get('pagination')
                page_num = pagination.get('currentPage')
                total_pages = pagination.get('totalPages')

                for entity in redux_state.get('entities') or []:
                    video_id = entity.get('id')
                    if video_id:
                        result = self.url_result(self._URL_TEMPLATE % video_id, BBCCoUkIE.ie_key())
                        if season:
                            result['season'] = season
                        yield result

                webpage = redux_state = None

                if single_page:
                    break

                page_num += 1
                page_url_template = pagination.get('pageUrl') or '?page=%s'
                url = compat_urlparse.urljoin(url, page_url_template % (page_num, ))

            if single_season:
                break

            next_season_id, season = (slices[season_n].get(k) for k in ('id', 'title'))
            url = compat_urlparse.urljoin(url, '?seriesId=' + next_season_id)
            webpage = redux_state = None


class BBCCoUkIPlayerEpisodesIE(BBCCoUkIPlayerPlaylistBaseIE):
    IE_NAME = 'bbc.co.uk:iplayer:episodes'
    _VALID_URL = BBCCoUkIPlayerPlaylistBaseIE._VALID_URL_TMPL % ('episodes', )
    _TESTS = [{
        'url': 'http://www.bbc.co.uk/iplayer/episodes/b05rcz9v',
        'info_dict': {
            'id': 'b05rcz9v',
            'title': 'The Disappearance',
            'description': 'md5:58eb101aee3116bad4da05f91179c0cb',
        },
        'playlist_mincount': 8,
        'skip': ' No episodes available',
    }, {
        # all seasons
        'url': 'https://www.bbc.co.uk/iplayer/episodes/b0by92vf/better-things',
        'info_dict': {
            'id': 'b0by92vf',
            'title': 'Better Things',
            'description': 'md5:5cb7b1811e046a57816a2f91ae41ca27',
        },
        'playlist_mincount': 50,
    }, {
        # explicit season
        'url': 'https://www.bbc.co.uk/iplayer/episodes/b0by92vf/better-things?seriesId=p08mxmgq',
        'info_dict': {
            'id': 'b0by92vf',
            'title': 'Better Things (Series 4)',
            'description': 'md5:5cb7b1811e046a57816a2f91ae41ca27',
        },
        'playlist_count': 10,
    }, {
        # all pages
        'url': 'https://www.bbc.co.uk/iplayer/episodes/m0004c4v/beechgrove',
        'info_dict': {
            'id': 'm0004c4v',
            'title': 'Beechgrove',
            'description': 'md5:a48ac6f75a6cd4bddff96ae215853b28',
        },
        'playlist_mincount': 35,
    }, {
        # explicit page
        'url': 'https://www.bbc.co.uk/iplayer/episodes/m0004c4v/beechgrove?page=2',
        'info_dict': {
            'id': 'm0004c4v',
            'title': 'Beechgrove',
            'description': 'md5:a48ac6f75a6cd4bddff96ae215853b28',
        },
        'playlist_mincount': 1,
    }]
    _PAGE_SIZE = 100
    _DESCRIPTION_KEY = 'synopsis'

    def _get_episode_image(self, episode):
        return self._get_default(episode, 'image')

    def _get_episode_field(self, episode, field):
        return self._get_default(episode, field)

    @staticmethod
    def _get_elements(data):
        return data['entities']['results']

    @staticmethod
    def _get_episode(element):
        return element.get('episode') or {}

    def _call_api(self, pid, per_page, page=1, series_id=None):
        variables = {
            'id': pid,
            'page': page,
            'perPage': per_page,
        }
        if series_id:
            variables['sliceId'] = series_id
        return self._download_json(
            'https://graph.ibl.api.bbc.co.uk/', pid, headers={
                'Content-Type': 'application/json'
            }, data=json.dumps({
                'id': '5692d93d5aac8d796a0305e895e61551',
                'variables': variables,
            }).encode('utf-8'))['data']['programme']


class BBCCoUkIPlayerGroupIE(BBCCoUkIPlayerPlaylistBaseIE):
    IE_NAME = 'bbc.co.uk:iplayer:group'
    _VALID_URL = BBCCoUkIPlayerPlaylistBaseIE._VALID_URL_TMPL % 'group'
    _TESTS = [{
        # Available for over a year unlike 30 days for most other programmes
        'url': 'http://www.bbc.co.uk/iplayer/group/p02tcc32',
        'info_dict': {
            'id': 'p02tcc32',
            'title': 'Bohemian Icons',
            'description': 'md5:683e901041b2fe9ba596f2ab04c4dbe7',
        },
        'playlist_mincount': 10,
    }, {
        # all pages
        'url': 'https://www.bbc.co.uk/iplayer/group/p081d7j7',
        'info_dict': {
            'id': 'p081d7j7',
            'title': 'Music in Scotland',
            'description': 'Perfomances in Scotland and programmes featuring Scottish acts.',
        },
        'playlist_mincount': 37,
    }, {
        # explicit page
        'url': 'https://www.bbc.co.uk/iplayer/group/p081d7j7?page=2',
        'info_dict': {
            'id': 'p081d7j7',
            'title': 'Music in Scotland',
            'description': 'Perfomances in Scotland and programmes featuring Scottish acts.',
        },
        'playlist_mincount': 1,
    }]
    _PAGE_SIZE = 200

    def _get_episode_image(self, episode):
        return self._get_default(episode, 'images', 'standard')

    def _get_episode_field(self, episode, field):
        return episode.get(field)

    @staticmethod
    def _get_elements(data):
        return data['elements']

    @staticmethod
    def _get_episode(element):
        return element

    def _call_api(self, pid, per_page, page=1, series_id=None):
        return self._download_json(
            'http://ibl.api.bbc.co.uk/ibl/v1/groups/%s/episodes' % pid,
            pid, query={
                'page': page,
                'per_page': per_page,
            })['group_episodes']

    @staticmethod
    def _extract_playlist_data(data):
        return data['group']

    def _get_playlist_title(self, data):
        return data.get('title')

    def _fetch_page(self, programme_id, per_page, series_id, page):
        elements = self._get_elements(self._call_api(
            programme_id, per_page, page + 1, series_id))
        for element in elements:
            episode = self._get_episode(element)
            episode_id = episode.get('id')
            if not episode_id:
                continue
            thumbnail = None
            image = self._get_episode_image(episode)
            if image:
                thumbnail = image.replace('{recipe}', 'raw')
            category = self._get_default(episode, 'labels', 'category')
            yield {
                '_type': 'url',
                'id': episode_id,
                'title': self._get_episode_field(episode, 'subtitle'),
                'url': 'https://www.bbc.co.uk/iplayer/episode/' + episode_id,
                'thumbnail': thumbnail,
                'description': self._get_description(episode),
                'categories': [category] if category else None,
                'series': self._get_episode_field(episode, 'title'),
                'ie_key': BBCCoUkIE.ie_key(),
            }

    def _entries(self, url, playlist_id, webpage=None):
        qs = compat_parse_qs(compat_urllib_parse_urlparse(url).query)
        series_id = qs.get('seriesId', [None])[-1]
        page = qs.get('page', [None])[-1]
        per_page = 36 if page else self._PAGE_SIZE
        fetch_page = functools.partial(self._fetch_page, playlist_id, per_page, series_id)
        return fetch_page(int(page) - 1) if page else OnDemandPagedList(fetch_page, self._PAGE_SIZE)
