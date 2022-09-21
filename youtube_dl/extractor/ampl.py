# coding: utf-8
from __future__ import unicode_literals

import re

from .common import InfoExtractor
from .jwplatform import JWPlatformIE

from ..utils import (
    clean_html,
    determine_ext,
    extract_attributes,
    ExtractorError,
    float_or_none,
    get_element_by_class,
    get_elements_by_class,
    int_or_none,
    js_to_json,
    merge_dicts,
    parse_duration,
    parse_resolution,
    strip_or_none,
    try_get,
    unified_strdate,
    update_url_query,
    url_or_none,
    urljoin,
)


class RepozytoriumEmbedIE(JWPlatformIE):
    # eg https://repozytorium.fn.org.pl/?q=pl/fnplayer/embed/10907
    _VALID_URL = r'(?:https?:)?//repozytorium\.fn\.org\.pl/\?q=[a-z]{2}/fnplayer/embed/(?P<id>\d+)'

    @classmethod
    def extract_urls(cls, webpage):
        matches = re.finditer(
            r'<iframe\b[^>]+?\bsrc=["\'](?P<url>%s)' % (cls._VALID_URL),
            webpage)
        # site's https is broken
        return [m.group('url').replace('https://', 'http://') for m in matches]

    def _find_jwplayer_data(self, webpage, video_id=None, transform_source=js_to_json):
        mobj = re.search(
            # allow embedded (...)
            r'(?s)jwplayer\((?P<quote>[\'"])[^\'" ]+(?P=quote)\)(?!</script>).*?\.setup\s*\((?P<options>.+)\)',
            webpage)
        if mobj:

            def xfs(js):
                # encodeURI("...") -> "..."
                js = re.sub(r'''\bencodeURI\s*\(\s*((["'])(?:\\\2|(?!\2).)+\2)\s*\)''', r'\1', js)
                return transform_source(js) if transform_source else js

            try:
                jwplayer_data = self._parse_json(mobj.group('options'),
                                                 video_id=video_id,
                                                 transform_source=xfs)
            except ExtractorError:
                pass
            else:
                if isinstance(jwplayer_data, dict):
                    return jwplayer_data

    def _real_extract(self, url):
        video_id = self._match_id(url)
        webpage = self._download_webpage(url, video_id)

        return self._extract_jwplayer_data(webpage, video_id=video_id, base_url=url, require_title=False)


class ArtMPlIE(InfoExtractor):
    _IE_DESC = 'Museum of Modern Art in Warsaw'
    _VALID_PATH = r'/(?:pl|en)/filmoteka/[^/]+/(?P<id>[\w-]+)'
    _VALID_URL = r'https?://(?:www\.)?artmuseum\.pl' + _VALID_PATH
    _TESTS = [
        {
            'url': 'https://artmuseum.pl/en/filmoteka/praca/robakowski-jozef-test',
            'info_dict': {
                'id': 'robakowski-jozef-test',
                'ext': 'mp4',
                'title': 'Józef Robakowski - Test',
                'description': 'This non-camera piece was made by puncturing several dozen holes in overexposed motion picture film. When projected, the film ‘lets through’ the (natural) powerful light of the cinematic...',
                'uploader': 'Museum of Modern Art in Warsaw',
            },
        }, {
            'note': 'iframe embed from repozytorium.fn.org.pl',
            'url': 'https://artmuseum.pl/en/filmoteka/praca/ll-natalia-piramida-2',
            'info_dict': {
                'id': '10907',
                'ext': 'mp4',
                'title': 'Natalia LL - Pyramid',
                'description': 'nieużyty materiał PKF nr 36079  Pyramid  In the 1970s, Natalia LL\'s artistic practice, hitherto centred primarily on photography, began to embrace also new fields, such as experimental film and...',
                'uploader': 'Museum of Modern Art in Warsaw',
            },
        }, {
            'note': 'playlist, Polish textual metadata',
            'url': 'https://artmuseum.pl/pl/filmoteka/artysci/jozef-robakowski',
            'info_dict': {
                'id': 'jozef-robakowski',
                'title': 'Filmoteka - Józef Robakowski',
                'description': 'md5:1d91d4b767b651e51dd3a83d04438be0',
                'uploader': 'Muzeum Sztuki Nowoczesnej w Warszawie',
            },
            'playlist_mincount': 13,
        },
    ]

    def _extract_video(self, url, webpage, video_id):
        title = self._og_search_title(webpage)
        player = self._search_regex(r'(<div\b[^>]+\bid\s*=\s*(["\'])video-player\2[^>]*>)', webpage, 'video player', default='')
        player = extract_attributes(player)
        if not player:
            entries = [RepozytoriumEmbedIE(self._downloader)._real_extract(embed_url) for embed_url in RepozytoriumEmbedIE.extract_urls(webpage)]
            if len(entries) == 1:
                return merge_dicts(entries[0], {'title': title})
            return self.playlist_result(entries, playlist_id=video_id, playlist_title=title)
        sources = self._parse_json(player.get('data-sources', '{}'), video_id)
        ratio = float_or_none(player.get('data-ratio'))

        def offby(txt, n):
            return ''.join(map(lambda c: chr(ord(c) + n), tuple(re.sub(r'\\(.)', r'\1', txt))))

        def chg_res(txt, o_res, n_res):
            return txt.replace('/%s/' % (o_res, ), '/%s/' % (n_res, ))

        formats = []
        x_formats = []
        found_720p = False
        for i, (res, vids) in enumerate(try_get(sources, lambda s: s.items(), list) or []):
            if res == '720p':
                found_720p = True
            height = parse_resolution(res).get('height')
            width = int_or_none(height, scale=ratio)
            f = []
            for v_fmt, v_url in try_get(vids, lambda v: v.items(), list) or []:
                v_url_txt = v_url
                v_url = url_or_none(v_url)
                if not v_url:
                    v_url = url_or_none(offby(v_url_txt, -1))
                if not v_url:
                    continue
                f.append({
                    'format_id': '_'.join((res, v_fmt)),
                    'url': v_url,
                    'ext': determine_ext(v_url),
                    'height': height,
                    'width': width,
                })
            formats.extend(f)
            # 720p may be available even if not mentioned
            if i == 0:
                height = 720
                h_txt = '%dp' % (height, )
                for fmt in f:
                    fmt = fmt.copy()
                    fmt.update({
                        'format_id': fmt['format_id'].replace('%s_' % (res, ), h_txt + '_'),
                        'url': fmt['url'].replace('/%s/' % (res, ), '/%s/' % (h_txt, )),
                        'height': height,
                        'width': int_or_none(height, scale=ratio),
                    })
                    x_formats.append(fmt)

        if not found_720p:
            self._check_formats(x_formats, video_id)
            formats.extend(x_formats)
        self._sort_formats(formats)

        thumbnail = urljoin(url, player.get('data-post'))
        metadata = clean_html(get_element_by_class('collection-description-data', webpage))
        lines = metadata.splitlines()
        metadata = []
        metaline = None
        for i, line in enumerate(lines):
            if ':' in line:
                if metaline:
                    metadata.append(tuple(metaline.split(':', 1)))
                metaline = line.lstrip()
            elif metaline:
                metaline += line
        if metaline:
            metadata.append(tuple(metaline.split(':', 1)))
        # only pl and en!
        pl2en = {
            'Rok powstania': 'Year',
            'Czas trwania': 'Duration',
            'Język': 'Language',
            'Oryginalne media': 'Source',
            'Data nabycia': 'Acquisition date',
            'Sposób nabycia': 'Acquisition',
            'Forma własności': 'Ownership form',
        }
        metadata = dict((pl2en.get(k, k), v) for k, v in metadata)
        return {
            'id': video_id,
            'title': title,
            'formats': formats,
            'duration': parse_duration(metadata.get('Duration').replace('\\', '')),
            'upload_date': unified_strdate(metadata.get('Acquisition date')),
            'release_year': int_or_none(metadata.get('Year')),
            'thumbnail': thumbnail,
        }

    def _extract_playlist(self, url, webpage, video_id):
        title = self._og_search_title(webpage, default=None)
        playlist = self._search_regex(
            r'''(?s)(<div\b[^>]+\bid\s*=\s*["']video-playlist\b.*(?:</div>\s*){3})''',
            webpage, 'video playlist', default=webpage)

        def entries(html):
            boxes = get_elements_by_class('box', html)
            for box in boxes:
                link = urljoin(url, self._search_regex(
                    r'''<a\b[^>]+\bhref\s*=\s*(["'])(?P<path>%s)\1''' % (self._VALID_PATH, ),
                    box, 'item link', group='path', default=None))
                if not link:
                    continue
                yield self.url_result(link)

        return self.playlist_result(entries(playlist), playlist_id=video_id, playlist_title=title)

    def _real_extract(self, url):
        video_id = self._match_id(url)
        if (self._downloader.params.get('age_limit') or 18) >= 18:
            url = update_url_query(url, {'age18': 'true'})
        webpage = self._download_webpage(url, video_id)
        info = (
            self._extract_video(url, webpage, video_id) if '/praca/' in url
            else self._extract_playlist(url, webpage, video_id))
        # fix up "actual title - Museum Name"
        title = info['title'].rsplit(' - ', 1) + [None]
        title, uploader = ((strip_or_none(x) or None) for x in title[:2])

        return merge_dicts({
            'title': title,
            'description': self._og_search_description(webpage),
            'uploader': uploader,
        }, info, {
            'thumbnail': self._og_search_thumbnail(webpage),
        })
