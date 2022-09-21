# coding: utf-8
from __future__ import unicode_literals

import itertools
import re

from .common import InfoExtractor
from ..compat import (
    compat_HTTPError,
    compat_urllib_error,
)
from ..utils import (
    determine_ext,
    dict_get,
    ExtractorError,
    int_or_none,
    float_or_none,
    js_to_json,
    parse_iso8601,
    remove_end,
    strip_or_none,
    try_get,
    url_or_none,
)


class TV2IE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?tv2\.no/(?:v\d*/|video/(?:[^/]+/){,3})(?P<id>\d+)'
    _TESTS = [{
        'url': 'http://www.tv2.no/v/1787176/',
        'info_dict': {
            'id': '1787176',
            'ext': 'mp4',
            'title': 'Så mye kan du spare på klesvasken',
            'description': 'TV 2 HJELPER DEG: Hvilket program på vaskemaskinen koster minst, og vasker det like godt?',
            'timestamp': 1663328629,
            'upload_date': '20220916',
            'duration': 117.0,
            'view_count': int,
            'categories': list,
        },
    }, {
        'url': 'https://www.tv2.no/video/nyhetene/tv-2-hjelper-deg/saa-mye-kan-du-spare-paa-klesvasken/1787176/',
        'only_matching': True,
    }, {
        'url': 'http://www.tv2.no/v/916509/',
        'info_dict': {
            'id': '916509',
            'ext': 'mp4',
            'title': 'Se Frode Gryttens hyllest av Steven Gerrard',
            'description': 'TV 2 Sportens huspoet tar avskjed med Liverpools kaptein Steven Gerrard.',
            'timestamp': 1431715610,
            'upload_date': '20150515',
            'duration': 156.967,
            'view_count': int,
            'categories': list,
        },
        'skip': 'Unable to download JSON metadata - content expired?',
    }, {
        'url': 'http://www.tv2.no/v2/916509',
        'only_matching': True,
    }]
    _API_DOMAIN = 'sumo.tv2.no'
    _METADATA_PATH = 'rest/assets'
    _METADATA_URL_TMPL = 'https://%s/%s/%s.json'
    _PROTOCOLS = ('HLS', 'DASH')
    _GEO_COUNTRIES = ['NO']

    def _download_metadata_json(self, video_id):
        asset, urlh = self._download_json_handle(
            self._METADATA_URL_TMPL % (self._API_DOMAIN, self._METADATA_PATH, video_id),
            video_id, expected_status=404)
        if urlh.getcode() == 404:
            error = compat_urllib_error.HTTPError(urlh.geturl(), 404, 'Not Found', urlh.info(), urlh)
            raise ExtractorError('Unable to download JSON metadata - content expired?', cause=error)
        return asset

    def _download_playback_json(self, video_id, protocol):
        return self._download_json(
            'https://api.%s/play/%s?stream=%s' % (self._API_DOMAIN, video_id, protocol),
            video_id, 'Downloading playback JSON',
            headers={'content-type': 'application/json'},
            data='{"device":{"id":"1-1-1","name":"Nettleser (HTML)"}}'.encode())

    @staticmethod
    def _get_data_items(d):
        return try_get(d, lambda x: x['streams'], list)

    @staticmethod
    def _get_thumbnails(a):
        return [{
            'id': type_,
            'url': thumb_url,
        } for type_, thumb_url in (a.get('images') or {}).items()]

    @staticmethod
    def _get_timestamp(a):
        return dict_get(a, ('live_broadcast_time', 'update_time'))

    def _real_extract(self, url):
        video_id = self._match_id(url)

        asset = self._download_metadata_json(video_id)
        title = asset.get('subtitle') or asset['title']
        is_live = asset.get('live') is True

        formats = []
        format_urls = set([])
        for protocol in self._PROTOCOLS:
            try:
                data = self._download_playback_json(video_id, protocol)['playback']
            except ExtractorError as e:
                if isinstance(e.cause, compat_HTTPError) and e.cause.code == 401:
                    error = self._parse_json(e.cause.read().decode(), video_id)['error']
                    error_code = error.get('code')
                    if error_code == 'ASSET_PLAYBACK_INVALID_GEO_LOCATION':
                        self.raise_geo_restricted(countries=self._GEO_COUNTRIES)
                    elif error_code == 'SESSION_NOT_AUTHENTICATED':
                        self.raise_login_required()
                    raise ExtractorError(error['description'])
                raise
            items = self._get_data_items(data) or []
            for item in items:
                video_url = try_get(item, lambda x: x['url'])
                if not video_url or video_url in format_urls:
                    continue
                format_id = '%s-%s' % (protocol.lower(), dict_get(item, ('type', 'mediaFormat')))
                if not self._is_valid_url(video_url, video_id, format_id):
                    continue
                format_urls.add(video_url)
                ext = determine_ext(video_url)
                if ext == 'f4m':
                    formats.extend(self._extract_f4m_formats(
                        video_url, video_id, f4m_id=format_id, fatal=False))
                elif ext == 'm3u8':
                    if not data.get('drmProtected'):
                        formats.extend(self._extract_m3u8_formats(
                            video_url, video_id, 'mp4',
                            'm3u8' if is_live else 'm3u8_native',
                            m3u8_id=format_id, fatal=False))
                elif ext == 'mpd':
                    formats.extend(self._extract_mpd_formats(
                        video_url, video_id, format_id, fatal=False))
                elif ext == 'ism' or video_url.endswith('.ism/Manifest'):
                    pass
                else:
                    formats.append({
                        'url': video_url,
                        'format_id': format_id,
                        'tbr': int_or_none(item.get('bitrate')),
                        'filesize': int_or_none(item.get('fileSize')),
                    })
        if not formats and data.get('drmProtected'):
            raise ExtractorError('This video is DRM protected.', expected=True)
        self._sort_formats(formats)

        thumbnails = [t for t in self._get_thumbnails(asset) if t['url']]

        return {
            'id': video_id,
            'url': video_url,
            'title': self._live_title(title) if is_live else title,
            'description': strip_or_none(asset.get('description')),
            'thumbnails': thumbnails,
            'timestamp': parse_iso8601(self._get_timestamp(asset)),
            'duration': float_or_none(asset.get('accurateDuration') or asset.get('duration')),
            'view_count': int_or_none(asset.get('views')),
            'categories': dict_get(asset, ('tags', 'keywords'), '').split(','),
            'formats': formats,
            'is_live': is_live,
        }


class TV2ArticleIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?tv2\.no/(?:\d{4}/\d{2}/\d{2}|(?!video/|v\d*/))(?:[^/]+/)+?(?P<id>\d+)'
    _TESTS = [{
        'url': 'http://www.tv2.no/2015/05/16/nyheter/alesund/krim/pingvin/6930542',
        'info_dict': {
            'id': '6930542',
            'title': 'Russen hetses etter pingvintyveri - innrømmer å ha åpnet luken på buret',
            'description': 'De fire siktede nekter fortsatt for å ha stjålet pingvinbabyene, men innrømmer å ha åpnet luken til de små kyllingene.',
        },
        'playlist_count': 2,
        'skip': 'Account needed for archive content',
    }, {
        'url': 'https://www.tv2.no/mening_og_analyse/dette-er-ikke-laerernes-ansvar/15120661/',
        'info_dict': {
            'id': '1788417',
            'ext': 'mp4',
            'title': 'Lærerstreiken: Derfor blir de ikke enige ',
            'description': 'Streiken kom i gang for alvor rundt skolestart i august. Den er allerede den lengste lærerstreiken vi har hatt i Norge.  Både elever og lærere er fortvilet og partene i streiken står bom fast. Hvorfor er det slik?',
            'timestamp': 1663671503,
            'upload_date': '20220920',
        },
    }, {
        'url': 'http://www.tv2.no/a/6930542',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        playlist_id = self._match_id(url)

        webpage = self._download_webpage(url, playlist_id)

        # Old embed pattern (looks unused nowadays)
        assets = re.findall(r'data-assetid=["\'](\d+)', webpage)

        if not assets:
            # New embed pattern
            for m in itertools.chain(
                    re.finditer(r'(?s)TV2ContentboxVideo\((\{.+?})\)', webpage),
                    re.finditer(r'(?s)TV2\s*.\s*TV2Video\s*\(\s*(\{[^}]+})\s*\)', webpage)):
                video = self._parse_json(
                    m.group(1), playlist_id, transform_source=js_to_json, fatal=False)
                if not video:
                    continue
                asset = video.get('assetId')
                if asset:
                    assets.append(asset)

        entries = [
            self.url_result('http://www.tv2.no/v/%s' % asset_id, 'TV2')
            for asset_id in assets]

        title = remove_end(self._og_search_title(webpage), ' - TV2.no')
        description = remove_end(self._og_search_description(webpage), ' - TV2.no')

        return self.playlist_result(entries, playlist_id, title, description)


class KatsomoIE(TV2IE):
    _VALID_URL = r'https?://(?:www\.)?(?:katsomo|mtv(uutiset)?)\.fi/(?:sarja/[0-9a-z-]+-\d+/[0-9a-z-]+-|(?:#!/)?jakso/(?:\d+/[^/]+/)?|video/prog)(?P<id>\d+)'
    _TESTS = [{
        'url': 'https://www.mtv.fi/sarja/mtv-uutiset-live-33001002003/lahden-pelicans-teki-kovan-ratkaisun-ville-nieminen-pihalle-1181321',
        'info_dict': {
            'id': '1181321',
            'ext': 'mp4',
            'title': 'Lahden Pelicans teki kovan ratkaisun – Ville Nieminen pihalle',
            'description': 'Päätöksen teki Pelicansin hallitus.',
            'timestamp': 1575116484,
            'upload_date': '20191130',
            'duration': 37.12,
            'view_count': int,
            'categories': list,
        },
        'params': {
            # m3u8 download
            'skip_download': True,
        },
    }, {
        'url': 'http://www.katsomo.fi/#!/jakso/33001005/studio55-fi/658521/jukka-kuoppamaki-tekee-yha-lauluja-vaikka-lentokoneessa',
        'only_matching': True,
    }, {
        'url': 'https://www.mtvuutiset.fi/video/prog1311159',
        'only_matching': True,
    }, {
        'url': 'https://www.katsomo.fi/#!/jakso/1311159',
        'only_matching': True,
    }]
    _API_DOMAIN = 'api.katsomo.fi'
    _METADATA_PATH = 'api/web/asset'
    _PROTOCOLS = ('HLS', 'MPD')
    _GEO_COUNTRIES = ['FI']

    def _download_metadata_json(self, video_id):
        return super(KatsomoIE, self)._download_metadata_json(video_id)['asset']

    def _download_playback_json(self, video_id, protocol):
        return self._download_json(
            'http://%s/%s/%s/play.json?protocol=%s&videoFormat=SMIL+ISMUSP' % (self._API_DOMAIN, self._METADATA_PATH, video_id, protocol),
            video_id, 'Downloading playback JSON')

    @staticmethod
    def _get_data_items(d):
        items = try_get(d, lambda x: x['items']['item'])
        if items and not isinstance(items, list):
            items = [items]
        return items

    @staticmethod
    def _get_thumbnails(a):
        return [{
            'id': thumbnail.get('@type'),
            'url': url_or_none(thumbnail.get('url')),
        } for _, thumbnail in (a.get('imageVersions') or {}).items()]

    @staticmethod
    def _get_timestamp(a):
        return a.get('createTime')


class MTVUutisetArticleIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)mtvuutiset\.fi/artikkeli/[^/]+/(?P<id>\d+)'
    _TESTS = [{
        'url': 'https://www.mtvuutiset.fi/artikkeli/tallaisia-vaurioita-viking-amorellassa-on-useamman-osaston-alla-vetta/7931384',
        'info_dict': {
            'id': '1311159',
            'ext': 'mp4',
            'title': 'Viking Amorellan matkustajien evakuointi on alkanut – tältä operaatio näyttää laivalla',
            'description': 'Viking Amorellan matkustajien evakuointi on alkanut – tältä operaatio näyttää laivalla',
            'timestamp': 1600608966,
            'upload_date': '20200920',
            'duration': 153.7886666,
            'view_count': int,
            'categories': list,
        },
        'params': {
            # m3u8 download
            'skip_download': True,
        },
    }, {
        # multiple Youtube embeds
        'url': 'https://www.mtvuutiset.fi/artikkeli/50-vuotta-subarun-vastaiskua/6070962',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        article_id = self._match_id(url)
        article = self._download_json(
            'http://api.mtvuutiset.fi/mtvuutiset/api/json/' + article_id,
            article_id)

        def entries():
            for video in (article.get('videos') or []):
                video_type = video.get('videotype')
                video_url = video.get('url')
                if not (video_url and video_type in ('katsomo', 'youtube')):
                    continue
                yield self.url_result(
                    video_url, video_type.capitalize(), video.get('video_id'))

        return self.playlist_result(
            entries(), article_id, article.get('title'), article.get('description'))
