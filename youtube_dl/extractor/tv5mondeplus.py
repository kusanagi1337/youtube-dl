# coding: utf-8
from __future__ import unicode_literals

from .common import InfoExtractor
from ..compat import (
    compat_str,
    compat_urllib_parse,
    compat_urllib_parse_urlparse,
)
from ..utils import (
    determine_ext,
    extract_attributes,
    ExtractorError,
    int_or_none,
    mimetype2ext,
    parse_duration,
    try_get,
    url_or_none,
)


class TV5MondePlusIE(InfoExtractor):
    IE_DESC = 'TV5MONDE+'
    _VALID_URL = r'https?://(?:www\.)?(?:tv5mondeplus|revoir\.tv5monde)\.com/toutes-les-videos/[^/]+/(?P<id>[^/?#]+)'
    _TESTS = [{
        # movie
        'url': 'https://revoir.tv5monde.com/toutes-les-videos/cinema/ceux-qui-travaillent',
        'md5': '32fa0cde16a4480d1251502a66856d5f',
        'info_dict': {
            'id': 'dc57a011-ec4b-4648-2a9a-4f03f8352ed3',
            'display_id': 'ceux-qui-travaillent',
            'ext': 'mp4',
            'title': 'Ceux qui travaillent',
            'description': 'md5:570e8bb688036ace873b2d50d24c026d',
            'upload_date': '20210819',
        },
        'skip': 'Redirect to home page - content no longer available?',
    }, {
        # series episode
        'url': 'https://revoir.tv5monde.com/toutes-les-videos/series-fictions/vestiaires-caro-actrice',
        'info_dict': {
            'id': '9e9d599e-23af-6915-843e-ecbf62e97925',
            'display_id': 'vestiaires-caro-actrice',
            'ext': 'mp4',
            'title': "Vestiaires - Caro actrice",
            'description': 'md5:db15d2e1976641e08377f942778058ea',
            'upload_date': '20210819',
            'series': "Vestiaires",
            'episode': 'Caro actrice',
        },
        'skip': 'Redirect to home page - content no longer available?',
    }, {
        # documentary episode with deferred format
        'url': 'https://revoir.tv5monde.com/toutes-les-videos/documentaires/dora-maar-entre-ombre-et-lumiere-dora-maar-entre-ombre-et-lumiere',
        'info_dict': {
            'id': '6890f99d-a79a-1625-0667-14ba542c7f74',
            'display_id': 'dora-maar-entre-ombre-et-lumiere-dora-maar-entre-ombre-et-lumiere',
            'ext': 'mp4',
            'title': 'Dora Maar, entre ombre et lumière',
            'description': 'md5:114cd8a9ed1090222f5710e8d47964ad',
            'upload_date': '20220919',
            'episode': 'Dora Maar, entre ombre et lumière',
        },
        'params': {
            'format': 'bestvideo',
            'skip_download': True,
        },
    }, {
        'url': 'https://revoir.tv5monde.com/toutes-les-videos/series-fictions/neuf-jours-en-hiver-neuf-jours-en-hiver',
        'only_matching': True,
    }, {
        'url': 'https://revoir.tv5monde.com/toutes-les-videos/info-societe/le-journal-de-la-rts-edition-du-30-01-20-19h30',
        'only_matching': True,
    }]
    _GEO_BYPASS = False

    def _real_extract(self, url):
        display_id = self._match_id(url)
        webpage, urlh = self._download_webpage_handle(url, display_id)

        if compat_urllib_parse_urlparse(urlh.geturl()).path == '/':
            raise ExtractorError('Redirect to home page - content no longer available?')

        if ">Ce programme n'est malheureusement pas disponible pour votre zone géographique.<" in webpage:
            self.raise_geo_restricted(countries=['FR'])

        title = episode = self._html_search_regex(r'<h1>([^<]+)', webpage, 'title')
        vpl_data = extract_attributes(self._search_regex(
            r'(<[^>]+class="video_player_loader"[^>]+>)',
            webpage, 'video player loader'))

        video_files = self._parse_json(
            vpl_data['data-broadcast'], display_id)
        formats = []

        def process_video_files(v):
            for video_file in v:
                v_url = try_get(video_file, lambda x: x['url'], compat_str)
                if not v_url:
                    continue
                if video_file.get('type') == 'application/deferred':
                    video_file = self._download_json(
                        'https://api.tv5monde.com/player/asset/%s/resolve' % (compat_urllib_parse.quote(v_url), ),
                        display_id, note='Downloading asset metadata', fatal=False) or []
                    process_video_files(video_file)
                    continue
                video_format = video_file.get('format') or mimetype2ext(video_file.get('type')) or determine_ext(v_url)
                if video_format == 'm3u8':
                    formats.extend(self._extract_m3u8_formats(
                        v_url, display_id, 'mp4', 'm3u8_native',
                        m3u8_id='hls', fatal=False))
                elif video_format == 'mpd':
                    formats.extend(self._extract_mpd_formats(
                        v_url, display_id, fatal=False))
                else:
                    formats.append({
                        'url': v_url,
                        'format_id': video_format,
                    })

        process_video_files(video_files)

        self._sort_formats(formats)

        metadata = self._parse_json(
            vpl_data['data-metadata'], display_id)
        duration = (int_or_none(try_get(metadata, lambda x: x['content']['duration']))
                    or parse_duration(self._html_search_meta('duration', webpage)))

        description = self._html_search_regex(
            r'(?s)<div[^>]+class=["\']episode-texte[^>]+>(.+?)</div>', webpage,
            'description', fatal=False)

        series = self._html_search_regex(
            r'<p[^>]+class=["\']episode-emission[^>]+>([^<]+)', webpage,
            'series', default=None)

        if series and series != title:
            title = '%s - %s' % (series, title)

        upload_date = self._search_regex(
            r'(?:date_publication|publish_date)["\']\s*:\s*["\'](\d{4}_\d{2}_\d{2})',
            webpage, 'upload date', default=None)
        if upload_date:
            upload_date = upload_date.replace('_', '')

        video_id = self._search_regex(
            (r'data-guid=["\']([\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12})',
             r'id_contenu["\']\s:\s*(\d+)'), webpage, 'video id',
            default=display_id)

        return {
            'id': video_id,
            'display_id': display_id,
            'title': title,
            'description': description,
            'thumbnail': url_or_none(vpl_data.get('data-image')),
            'duration': duration,
            'upload_date': upload_date,
            'formats': formats,
            'series': series,
            'episode': episode,
        }
