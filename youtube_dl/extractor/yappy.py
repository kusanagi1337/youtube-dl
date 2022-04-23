# coding: utf-8
from __future__ import unicode_literals

import re

from .common import InfoExtractor
from ..compat import (
    compat_parse_qs,
    compat_urlparse,
)
from ..utils import (
    determine_ext,
    dict_get,
    ExtractorError,
    float_or_none,
    get_element_by_id,
    int_or_none,
    parse_iso8601,
    strip_or_none,
    str_or_none,
    try_get,
    update_url_query,
    url_or_none,
)


class YappyIE(InfoExtractor):
    _VALID_URL = r'(?:https?://yappy\.media/video/|yappy:)(?P<id>[\da-fA-F]{32})'
    _TESTS = [{
        'url': 'https://yappy.media/video/47fea6d8586f48d1a0cf96a7342aabd2',
        'md5': '99f4b157733f56a07cc7484ae7b2c223',
        'info_dict': {
            'id': '47fea6d8586f48d1a0cf96a7342aabd2',
            'title': '«Куда нажимать? Как снимать? Смотри видос и погнали!🤘🏻',
            'ext': 'mp4',
            'upload_date': '20211117',
            'description': 'Видео пользователя @YAPPY',
            'timestamp': 1637152871,
            'uploader': 'YAPPY',
            'uploader_id': '59a0c8c485e5410b9c43474bf4c6a373',
        },
    }]

    def _get_NEXT_DATA(self, video_id, webpage):
        data = get_element_by_id('__NEXT_DATA__', webpage)
        data = self._parse_json(data or '{}', video_id)
        data = try_get(data, lambda x: x['props'], dict) or {}
        data = dict_get(data, ('pageProps', 'initialState'))
        return data if isinstance(data, dict) else {}

    def _real_extract(self, url):
        video_id = self._match_id(url)
        webpage = self._download_webpage(url, video_id)

        info = self._search_json_ld(webpage, video_id, default={})

        title = re.split(r'\s*\|\s*', (
            self._html_search_regex(
                r'(?i)<title\b[^>]*>([^>]+?)\s*</title>', webpage,
                'title', fatal=False)
            or self._og_search_title(webpage, fatal=False)
            or ''), 1)[-1]
        title = title or self._generic_title(url)

        info.update({
            'id': video_id,
            'description': info.get('title'),
            'title': title,
        })

        video_data = self._get_NEXT_DATA(video_id, webpage)

        fmt_url = try_get(video_data, lambda x: url_or_none(x['data']['link']))
        if fmt_url is not None:
            video_data = video_data['data']
        else:
            fmt_url = try_get(video_data, lambda x: url_or_none(x['openGraphParameters']['params']['link']))
            if fmt_url is not None:
                video_data = video_data['openGraphParameters']['params']
            else:
                msg = ['Unable to extract link']
                if video_data.get('isError', False):
                    msg += ['error']
                msg += [video_data.get('errorMessage'), video_data.get('errorDescription'), ]
                msg = ': '.join(filter(None, msg))
                raise ExtractorError(msg)

        ext = determine_ext(fmt_url)
        formats = []
        if ext == 'm3u8':
            formats.extend(self._extract_m3u8_formats(
                fmt_url, video_id, 'mp4', entry_protocol='m3u8_native',
                m3u8_id='hls', fatal=False))
        elif ext == 'mpd':
            formats.extend(self._extract_mpd_formats(
                fmt_url, video_id, mpd_id='dash', fatal=False))
        else:
            formats.append({
                'url': fmt_url,
            })
        self._sort_formats(formats)

        for name, site_name, constrain in (('description', 'description', strip_or_none),
                                           ('thumbnail', 'thumbnail', url_or_none),
                                           ('timestamp', 'publishedAt', parse_iso8601),
                                           ('view_count', 'viewsCount', int_or_none), ):
            if name not in info:
                info[name] = constrain(video_data.get(site_name))

        info.update({
            'formats': formats,
            'like_count': int_or_none(video_data.get('likesCount')),
            'comment_count': int_or_none(video_data.get('commentsCount')),
            'repost_count': int_or_none(video_data.get('sharingCount')),
            'categories': list(filter(None, try_get(video_data.get('categories'),
                                                    lambda x: [n['name'] for n in x]))),
        })

        creator = try_get(video_data, lambda x: x['creator'], dict)
        if creator:
            info.update({
                'uploader_id': str_or_none(creator.get('uuid')),
                'uploader': str_or_none(creator.get('nickname')),
            })

        audio = try_get(video_data, lambda x: x['audio'], dict)
        if audio:
            audio_fmt = url_or_none(audio.get('link'))
            if audio_fmt:
                info['formats'].append({'url': audio_fmt, 'vcodec': 'none', })
            info.update({
                'duration': float_or_none(audio.get('duration'), 1000),
            })

        return info


class YappyProfileIE(InfoExtractor):
    _VALID_URL = r'https?://yappy\.media/profile/(?P<id>[\da-fA-F]{32})'
    _TESTS = [{
        'url': 'https://yappy.media/profile/5a44cef4ca6f4aa782e0225a883e225d',
        'info_dict': {
            'id': '5a44cef4ca6f4aa782e0225a883e225d',
        },
        'playlist_mincount': 25,
    }]
    _PLAYLIST_URL_TEMPLATE = 'https://yappy.media/api/video-list/%s'

    def _real_extract(self, url):
        playlist_id = self._match_id(url)

        def get_page(url, default=None):
            return compat_parse_qs(compat_urlparse.urlparse(url).query).get('page', [default])[-1]

        def get_entries(start, playlist_id):
            next = start
            while next:
                page = get_page(next, '1')
                playlist_json = self._download_json(
                    next, playlist_id, note='Downloading playlist JSON' + (' - %s' % (page, ) if page != '1' else ''),
                    fatal=False) or {}

                for vid in try_get(playlist_json, lambda x: x['results'], list) or []:
                    if not isinstance(vid, dict):
                        continue
                    vid = vid.get('uuid')
                    if vid:
                        # TODO: extract some metadata
                        yield self.url_result('yappy:' + vid, ie='Yappy')

                # a URL that has the right page number
                api_next = playlist_json.get('next')
                if not api_next:
                    return
                next_page = get_page(api_next)
                if next_page is None or page == next_page:
                    return
                next = update_url_query(next, {'page': next_page, })

        return self.playlist_result(
            (x for x in get_entries(self._PLAYLIST_URL_TEMPLATE % (playlist_id, ), playlist_id)),
            playlist_id)
