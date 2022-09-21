# coding: utf-8
from __future__ import unicode_literals

from datetime import datetime
import base64
from .common import InfoExtractor
from ..compat import (
    compat_kwargs,
    compat_str,
)
from ..utils import (
    clean_html,
    error_to_compat_str,
    get_element_by_class,
    GeoRestrictedError,
    HEADRequest,
    int_or_none,
    url_or_none,
    urlencode_postdata,
)


# what strip_or_none() should have been
def txt_or_none(v, default=None, chars=None):
    return (v.strip(chars) or default) if isinstance(v, compat_str) else default


class TenPlayIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?10play\.com\.au/(?:[^/]+/)+(?P<id>tpv\d{6}[a-z]{5})'
    _NETRC_MACHINE = '10play'
    _TESTS = [{
        'url': 'https://10play.com.au/neighbours/web-extras/season-39/nathan-borg-is-the-first-aussie-actor-with-a-cochlear-implant-to-join-neighbours/tpv210128qupwd',
        'info_dict': {
            'id': '6226844312001',
            'ext': 'mp4',
            'title': 'Nathan Borg Is The First Aussie Actor With A Cochlear Implant To Join Neighbours',
            'alt_title': 'Nathan Borg Is The First Aussie Actor With A Cochlear Implant To Join Neighbours',
            'description': 'md5:a02d0199c901c2dd4c796f1e7dd0de43',
            'duration': 186,
            'season': 39,
            'series': 'Neighbours',
            'thumbnail': r're:https://.*\.jpg',
            'uploader': 'Channel 10',
            'age_limit': 15,
            'timestamp': 1611810000,
            'upload_date': '20210128',
            'uploader_id': '2199827728001',
        },
        'params': {
            'skip_download': True,
        },
        'skip': 'Only available in Australia',
    }, {
        'url': 'https://10play.com.au/todd-sampsons-body-hack/episodes/season-4/episode-7/tpv200921kvngh',
        'info_dict': {
            'id': '6192880312001',
            'ext': 'mp4',
            'title': "Todd Sampson's Body Hack - S4 Ep. 2",
            'description': 'md5:fa278820ad90f08ea187f9458316ac74',
            'age_limit': 15,
            'timestamp': 1600770600,
            'upload_date': '20200922',
            'uploader': 'Channel 10',
            'uploader_id': '2199827728001'
        },
        'params': {
            'skip_download': True,
        },
        'skip': 'Only available in Australia',
    }, {
        'url': 'https://10play.com.au/how-to-stay-married/web-extras/season-1/terrys-talks-ep-1-embracing-change/tpv190915ylupc',
        'only_matching': True,
    }]
    _GEO_BYPASS = False

    _AUS_AGES = {
        'G': 0,
        'PG': 15,
        'M': 15,
        'MA': 15,
        'MA15+': 15,
        'R': 18,
        'X': 18
    }

    def _get_bearer_token(self, video_id):
        username, password = self._get_login_info()
        if username is None or password is None:
            self.raise_login_required('Your 10play account\'s details must be provided with --username and --password.')
        timestamp = datetime.now().strftime('%Y%m%d000000')
        auth_header = base64.b64encode(timestamp.encode('ascii'))
        data = self._download_json(
            'https://10play.com.au/api/user/auth', video_id, 'Getting bearer token',
            headers={'X-Network-Ten-Auth': auth_header, },
            data=urlencode_postdata({
                'email': username,
                'password': password,
            }))
        return 'Bearer ' + data['jwt']['accessToken']

    @staticmethod
    def raise_geo_restricted(*args, **kwargs):
        kwargs.setdefault('countries', ['AU'])
        super(TenPlayIE, TenPlayIE).raise_geo_restricted(*args, **compat_kwargs(kwargs))

    def _download_webpage_handle(self, url_or_request, video_id, *args, **kwargs):
        res = super(TenPlayIE, self)._download_webpage_handle(url_or_request, video_id, *args, **kwargs)
        if res is False:
            return res
        try:
            if 'is not available in your region' in clean_html(get_element_by_class("iserror", res[0]) or ''):
                self.raise_geo_restricted()
        except GeoRestrictedError as e:
            fatal = kwargs.get('fatal', True)
            if fatal:
                raise
            self.report_warning(error_to_compat_str(e))
        except Exception:
            pass
        return res

    def _real_extract(self, url):
        content_id = self._match_id(url)
        data = self._download_json(
            'https://10play.com.au/api/v1/videos/' + content_id, content_id)
        headers = {}
        if data.get('memberGated') is True:
            headers['Authorization'] = self._get_bearer_token(content_id)

        video_url = self._download_json(
            data.get('playbackApiEndpoint'), content_id, 'Downloading video JSON',
            headers=headers).get('source')
        m3u8_url = self._request_webpage(HEADRequest(video_url), content_id).geturl()
        if '10play-not-in-oz' in m3u8_url:
            self.raise_geo_restricted()
        formats = self._extract_m3u8_formats(m3u8_url, content_id, 'mp4')
        self._sort_formats(formats)
        sttl_url = url_or_none(data.get('captionUrl'))

        return {
            'id': txt_or_none(data.get('altId'), content_id),
            'title': txt_or_none(data.get('subtitle'), self._generic_title(url)),
            'formats': formats,
            'subtitles': sttl_url and {'en': [{'url': sttl_url}]},
            'duration': int_or_none(data.get('duration')),
            'alt_title': txt_or_none(data.get('title')),
            'description': txt_or_none(data.get('description')),
            'age_limit': self._AUS_AGES.get(data.get('classification')),
            'series': txt_or_none(data.get('tvShow')),
            'season': int_or_none(data.get('season')),
            'episode_number': int_or_none(data.get('episode')),
            'timestamp': int_or_none(data.get('published')),
            'thumbnail': url_or_none(data.get('imageUrl')),
            'uploader': 'Channel 10',
            'uploader_id': '2199827728001',
        }
