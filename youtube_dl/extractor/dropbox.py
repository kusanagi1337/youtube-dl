# coding: utf-8
from __future__ import unicode_literals

import os.path
import re

from .common import InfoExtractor
from ..compat import (
    compat_urllib_parse_unquote,
    compat_urllib_parse_urlencode,
)
from ..utils import (
    determine_ext,
    ExtractorError,
    try_get,
    update_url_query,
    url_basename,
    url_or_none,
)


class DropboxIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?dropbox[.]com/sh?/(?P<id>[a-zA-Z0-9]{15})/.*'
    _TESTS = [
        {
            'url': 'https://www.dropbox.com/s/nelirfsxnmcfbfh/youtube-dl%20test%20video%20%27%C3%A4%22BaW_jenozKc.mp4?dl=0',
            'info_dict': {
                'id': 'nelirfsxnmcfbfh',
                'ext': 'mp4',
                'title': 'youtube-dl test video \'ä"BaW_jenozKc'
            }
        }, {
            'url': 'https://www.dropbox.com/sh/662glsejgzoj9sr/AAByil3FGH9KFNZ13e08eSa1a/Pregame%20Ceremony%20Program%20PA%2020140518.m4v',
            'only_matching': True,
        },
    ]

    def _real_extract(self, url):
        video_id = self._match_id(url)
        webpage = self._download_webpage(url, video_id)
        fn = compat_urllib_parse_unquote(url_basename(url))
        title = os.path.splitext(fn)[0]

        password = self._downloader.params.get('videopassword')
        if (self._og_search_title(webpage) == 'Dropbox - Password Required'
                or 'Enter the password for this link' in webpage):

            if password:
                content_id = self._search_regex(
                    r'''content_id=(.*?)["']''', webpage, 'content_id')
                payload = {
                    'is_xhr': 'true',
                    't': self._get_cookies("https://www.dropbox.com").get("t").value,
                    'content_id': content_id,
                    'password': password,
                    'url': url,
                }

                response = self._download_json(
                    'https://www.dropbox.com/sm/auth', video_id,
                    'POSTing video password',
                    data=compat_urllib_parse_urlencode(payload),
                    headers={'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'})

                if response.get('status') != 'authed':
                    raise ExtractorError('Authentication failed!', expected=True)
                webpage = self._download_webpage(url, video_id)
            elif self._get_cookies('https://dropbox.com').get('sm_auth'):
                webpage = self._download_webpage(url, video_id)
            else:
                raise ExtractorError(
                    'Password protected video, use --video-password <password>',
                    expected=True)

        formats = []
        json_string = next(
            (x for x in (
                m.group(1) for m in re.finditer(
                    r'(?s)InitReact\s*\.\s*mountComponent\s*\(.*?,\s*(\s*\{.+?\})\s*?\)',
                    webpage))
                if '/react/file_viewer/' in x), '{}')
        info_json = self._parse_json(json_string, video_id).get('props')
        get_url = lambda x: x['file']['preview']['content']['transcode_url']
        transcode_url = next(
            (u for u in (
                url_or_none(try_get(info_json, g))
                for g in (get_url, lambda x: get_url(x['preview'])))
                if u), None)
        if transcode_url:
            # TODO: formats, subtitles = self._extract_m3u8_formats_and_subtitles(transcode_url, video_id)
            ext = determine_ext(fn)
            formats.extend(
                self._extract_m3u8_formats(
                    transcode_url, video_id, ext, entry_protocol='m3u8_native', fatal=False))

        # if downloads enabled we can get the original file
        roles = try_get(info_json,
                        lambda x: x['sharePermission']['canDownloadRoles'], list)
        if 'anonymous' in roles or []:
            video_url = update_url_query(url, {'dl': '1'})
            formats.append({
                'url': video_url,
                'format_id': 'original',
                'format_note': 'Original',
                'quality': 1})
        self._sort_formats(formats)

        return {
            'id': video_id,
            'title': title,
            'formats': formats,
            # 'subtitles': subtitles
        }
