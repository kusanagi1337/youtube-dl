# coding: utf-8
from __future__ import unicode_literals

import base64
import json
import re

from .common import InfoExtractor
from ..compat import compat_str
from ..utils import (
    determine_ext,
    get_element_by_id,
    mimetype2ext,
    parse_duration,
    qualities,
    try_get,
    url_or_none,
)


class ImdbIE(InfoExtractor):
    IE_NAME = 'imdb'
    IE_DESC = 'Internet Movie Database trailers'
    _VALID_URL = r'https?://(?:www|m)\.imdb\.com/(?:video|title|list).*?[/-]vi(?P<id>\d+)'

    _TESTS = [{
        'url': 'http://www.imdb.com/video/imdb/vi2524815897',
        'md5': '471594d511a4dee8d71cea96dd72b1ad',
        'info_dict': {
            'id': '2524815897',
            'ext': 'mp4',
            'title': 'Ice Age 4: Continental Drift',
            'description': 'md5:87bd0bdc61e351f21f20d2d7441cb4e7',
            'duration': 152,
        },
        'params': {
            'format': '[format_id!^=hls]',
        }
    }, {
        'url': 'http://www.imdb.com/video/_/vi2524815897',
        'only_matching': True,
    }, {
        'url': 'http://www.imdb.com/title/tt1667889/?ref_=ext_shr_eml_vi#lb-vi2524815897',
        'only_matching': True,
    }, {
        'url': 'http://www.imdb.com/title/tt1667889/#lb-vi2524815897',
        'only_matching': True,
    }, {
        'url': 'http://www.imdb.com/videoplayer/vi1562949145',
        'only_matching': True,
    }, {
        'url': 'http://www.imdb.com/title/tt4218696/videoplayer/vi2608641561',
        'only_matching': True,
    }, {
        'url': 'https://www.imdb.com/list/ls009921623/videoplayer/vi260482329',
        'only_matching': True,
    }]

    @staticmethod
    def get_value(data, prop, value_name='value', expected_type=compat_str):
        return try_get(data, lambda x: x[prop][value_name], expected_type)

    def _extract_formats(self, fmt_list, video_id):
        if not isinstance(fmt_list, (list, tuple)):
            return []
        quality = qualities(('SD', '480p', '720p', '1080p'))
        formats = []
        for encoding in fmt_list:
            video_url = url_or_none(try_get(encoding, lambda x: x['url']))
            if not video_url:
                continue
            ext = mimetype2ext(encoding.get(
                'mimeType')) or determine_ext(video_url)
            if ext == 'm3u8':
                formats.extend(self._extract_m3u8_formats(
                    video_url, video_id, 'mp4', entry_protocol='m3u8_native',
                    preference=1, m3u8_id='hls', fatal=False))
                continue
            format_id = (
                self.get_value(encoding, 'displayName')
                or encoding.get('definition'))
            formats.append({
                'format_id': format_id,
                'url': video_url,
                'ext': ext,
                'quality': quality(format_id),
                'language': encoding.get('language'),
            })
            return formats

    def _real_extract(self, url):
        video_id = self._match_id(url)

        data = self._download_json(
            'https://www.imdb.com/ve/data/VIDEO_PLAYBACK_DATA', video_id,
            query={
                'key': base64.b64encode(json.dumps({
                    'type': 'VIDEO_PLAYER',
                    'subType': 'FORCE_LEGACY',
                    'id': 'vi%s' % video_id,
                }).encode()).decode(),
            })[0]

        formats = self._extract_formats(try_get(data, lambda x: x['videoLegacyEncodings'], list), video_id)

        webpage = self._download_webpage(
            'https://www.imdb.com/video/vi' + video_id, video_id)
        video_metadata = try_get(
            self._parse_json(get_element_by_id('__NEXT_DATA__', webpage), video_id, fatal=False),
            lambda x: x['props']['pageProps']['videoPlaybackData']['video'],
            dict)
        if video_metadata:

            title = try_get(video_metadata,
                            lambda x: x['primaryTitle']['titleText']['text'],
                            compat_str)
            alt_title = self.get_value(video_metadata, 'name')
            if not title:
                title = alt_title
            if title == alt_title:
                alt_title = None
            if not alt_title:
                alt_title = try_get(
                    video_metadata,
                    lambda x: x['primaryTitle']['originalTitleText']['text'],
                    compat_str)
                if title == alt_title:
                    alt_title = None

            description = self.get_value(video_metadata, 'description')

            formats.extend(self._extract_formats(video_metadata.get('playbackURLs'), video_id))
            self._remove_duplicate_formats(formats)

            thumbnail = url_or_none(self.get_value(video_metadata, 'thumbnail', value_name='url'))
            duration = self.get_value(video_metadata, 'runtime', expected_type=(int, float))
            if duration is not None:
                duration = parse_duration(
                    '%g%s' % (duration, self.get_value(video_metadata, 'runtime', value_name='unit') or 's'))

        else:
            video_metadata = self._parse_json(self._search_regex(
                r'args\.push\(\s*({.+?})\s*\)\s*;', webpage,
                'video metadata', fatal=False) or '{}', video_id)

            video_info = try_get(video_metadata, lambda x: x['VIDEO_INFO'], dict) or {}
            info = try_get(
                video_info, lambda x: x[list(video_info.keys())[0]][0], dict) or {}
            title = info.get('videoTitle')
            alt_title = info.get('videoSubTitle')
            description = info.get('videoDescription')
            thumbnail = url_or_none(try_get(
                info, lambda x: x['videoSlate']['source']))
            duration = parse_duration(info.get('videoRuntime'))

        if not title:
            title = (
                self._html_search_meta(('og:title', 'twitter:title'), webpage)
                or self._html_search_regex(r'(?s)<title\b[^>]*>(.+?)</title>', webpage, 'title'))

        self._sort_formats(formats)

        return {
            'id': video_id,
            'title': title,
            'alt_title': alt_title,
            'formats': formats,
            'description': description,
            'thumbnail': thumbnail,
            'duration': duration,
        }


class ImdbListIE(InfoExtractor):
    IE_NAME = 'imdb:list'
    IE_DESC = 'Internet Movie Database lists'
    _VALID_URL = r'https?://(?:www\.)?imdb\.com/list/ls(?P<id>\d{9})(?!/videoplayer/vi\d+)'
    _TEST = {
        'url': 'https://www.imdb.com/list/ls009921623/',
        'info_dict': {
            'id': '009921623',
            'title': 'The Bourne Legacy',
            'description': 'A list of trailers, clips, and more from The Bourne Legacy, starring Jeremy Renner and Rachel Weisz.',
        },
        'playlist_count': 8,
    }

    def _real_extract(self, url):
        list_id = self._match_id(url)
        webpage = self._download_webpage(url, list_id)
        entries = [
            self.url_result('http://www.imdb.com' + m, 'Imdb')
            for m in re.findall(r'href="(/list/ls%s/videoplayer/vi[^"]+)"' % list_id, webpage)]

        list_title = self._html_search_regex(
            r'<h1[^>]+class="[^"]*header[^"]*"[^>]*>(.*?)</h1>',
            webpage, 'list title')
        list_description = self._html_search_regex(
            r'<div[^>]+class="[^"]*list-description[^"]*"[^>]*><p>(.*?)</p>',
            webpage, 'list description')

        return self.playlist_result(entries, list_id, list_title, list_description)
