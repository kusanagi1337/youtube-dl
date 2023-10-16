# coding: utf-8
from __future__ import unicode_literals

import re
import itertools

from math import isinf

from .common import (
    InfoExtractor,
    SearchInfoExtractor,
)
from ..compat import (
    compat_kwargs,
    compat_str,
    compat_urlparse,
    compat_urllib_parse_unquote,
    compat_urllib_request,
)
from ..utils import (
    clean_html,
    determine_ext,
    extract_attributes,
    ExtractorError,
    get_element_by_class,
    get_element_by_id,
    get_elements_by_class,
    int_or_none,
    join_nonempty,
    LazyList,
    merge_dicts,
    parse_count,
    parse_duration,
    remove_end,
    remove_start,
    T,
    traverse_obj,
    try_call,
    txt_or_none,
    url_basename,
    urljoin,
)


class XVideosIE(InfoExtractor):
    _VALID_URL = r'''(?x)
                    (?:
                        https?://
                            (?:
                                # xvideos\d+\.com redirects to xvideos.com
                                # (?P<country>[a-z]{2})\.xvideos.com too: catch it anyway
                                (?:[^/]+\.)?xvideos\.com/(?:video|prof-video-click/model/[^/]+/)|
                                (?:www\.)?xvideos\.es/video|
                                (?:www|flashservice)\.xvideos\.com/embedframe/|
                                static-hw\.xvideos\.com/swf/xv-player\.swf\?.*?\bid_video=
                            )|
                        xvideos:
                    )(?P<id>\d+)
                 '''
    _TESTS = [{
        'url': 'http://www.xvideos.com/video4588838/biker_takes_his_girl',
        'md5': '14cea69fcb84db54293b1e971466c2e1',
        'info_dict': {
            'id': '4588838',
            'ext': 'mp4',
            'title': 'Biker Takes his Girl',
            'duration': 108,
            'age_limit': 18,
        },
        'skip': 'Sorry, this video has been deleted',
    }, {
        'url': 'https://www.xvideos.com/video78250973/hot_blonde_gets_excited_in_the_middle_of_the_club.',
        'md5': '0bc6e46ef55907533ffa0542e45958b6',
        'info_dict': {
            'id': '78250973',
            'ext': 'mp4',
            'title': 'Hot blonde gets excited in the middle of the club.',
            'uploader': 'Deny Barbie Official',
            'age_limit': 18,
            'duration': 302,
        },
    }, {
        # Broken HLS formats
        'url': 'https://www.xvideos.com/video65982001/what_s_her_name',
        'md5': '18ff7d57d4edc3c908fc5b06166dd63d',
        'info_dict': {
            'id': '65982001',
            'ext': 'mp4',
            'title': 'what\'s her name?',
            'uploader': 'Skakdjskdk',
            'age_limit': 18,
            'duration': 120,
            'thumbnail': r're:^https://img-[a-z]+.xvideos-cdn.com/.+\.jpg',
        }
    }, {
        # from PR #30689
        'url': 'https://www.xvideos.com/video50011247/when_girls_play_-_adriana_chechik_abella_danger_-_tradimento_-_twistys',
        'md5': 'aa54f96311768b3a8bfe54b8c8fda070',
        'info_dict': {
            'id': '50011247',
            'ext': 'mp4',
            'title': 'When Girls Play - (Adriana Chechik, Abella Danger) - Betrayal - Twistys',
            'duration': 720,
            'age_limit': 18,
            'tags': ['lesbian', 'teen', 'hardcore', 'latina', 'rough', 'squirt', 'big-ass', 'cheater', 'twistys', 'cheat', 'ass-play', 'when-girls-play'],
            'creator': 'Twistys',
            'uploader': 'Twistys',
            'uploader_url': 'https://www.xvideos.com/channels/twistys1',
            'cast': [{'given_name': 'Adriana Chechik', 'url': 'https://www.xvideos.com/pornstars/adriana-chechik'}, {'given_name': 'Abella Danger', 'url': 'https://www.xvideos.com/pornstars/abella-danger'}],
            'view_count': 'lambda c: c >= 4038715',
            'like_count': 'lambda c: c >= 8800',
            'dislike_count': 'lambda c: c >= 3100',
        },
    }, {
        'url': 'https://flashservice.xvideos.com/embedframe/4588838',
        'only_matching': True,
    }, {
        'url': 'http://static-hw.xvideos.com/swf/xv-player.swf?id_video=4588838',
        'only_matching': True,
    }, {
        'url': 'http://xvideos.com/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'https://xvideos.com/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'https://xvideos.es/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'https://www.xvideos.es/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'http://xvideos.es/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'http://www.xvideos.es/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'http://fr.xvideos.com/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'https://fr.xvideos.com/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'http://it.xvideos.com/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'https://it.xvideos.com/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'http://de.xvideos.com/video4588838/biker_takes_his_girl',
        'only_matching': True
    }, {
        'url': 'https://de.xvideos.com/video4588838/biker_takes_his_girl',
        'only_matching': True
    }]

    @classmethod
    def suitable(cls, url):
        EXCLUDE_IE = (XVideosRelatedIE, )
        return (False if any(ie.suitable(url) for ie in EXCLUDE_IE)
                else super(XVideosIE, cls).suitable(url))

    def _real_extract(self, url):
        video_id = self._match_id(url)

        webpage = self._download_webpage(
            'https://www.xvideos.com/video%s/0' % video_id, video_id)

        mobj = re.search(r'<h1 class="inlineError">(.+?)</h1>', webpage)
        if mobj:
            raise ExtractorError('%s said: %s' % (self.IE_NAME, clean_html(mobj.group(1))), expected=True)

        title = self._html_search_regex(
            (r'<title>(?P<title>.+?)\s+-\s+XVID',
             r'setVideoTitle\s*\(\s*(["\'])(?P<title>(?:(?!\1).)+)\1'),
            webpage, 'title', default=None,
            group='title') or self._og_search_title(webpage)

        thumbnails = []
        for preference, thumbnail in enumerate(('', '169')):
            thumbnail_url = self._search_regex(
                r'setThumbUrl%s\(\s*(["\'])(?P<thumbnail>(?:(?!\1).)+)\1' % thumbnail,
                webpage, 'thumbnail', default=None, group='thumbnail')
            if thumbnail_url:
                thumbnails.append({
                    'url': thumbnail_url,
                    'preference': preference,
                })

        duration = int_or_none(self._og_search_property(
            'duration', webpage, default=None)) or parse_duration(
            self._search_regex(
                r'''<span [^>]*\bclass\s*=\s*["']duration\b[^>]+>.*?(\d[^<]+)''',
                webpage, 'duration', fatal=False))

        formats = []

        video_url = compat_urllib_parse_unquote(self._search_regex(
            r'flv_url=(.+?)&', webpage, 'video URL', default=''))
        if video_url:
            formats.append({
                'url': video_url,
                'format_id': 'flv',
            })

        for kind, _, format_url in re.findall(
                r'setVideo([^(]+)\((["\'])(http.+?)\2\)', webpage):
            format_id = kind.lower()
            if format_id == 'hls':
                hls_formats = self._extract_m3u8_formats(
                    format_url, video_id, 'mp4',
                    entry_protocol='m3u8_native', m3u8_id='hls', fatal=False)
                self._check_formats(hls_formats, video_id)
                formats.extend(hls_formats)
            elif format_id in ('urllow', 'urlhigh'):
                formats.append({
                    'url': format_url,
                    'format_id': '%s-%s' % (determine_ext(format_url, 'mp4'), format_id[3:]),
                    'quality': -2 if format_id.endswith('low') else None,
                })

        self._sort_formats(formats)

        # adapted from PR #30689
        ignore_tags = set(('xvideos', 'xvideos.com', 'x videos', 'x video', 'porn', 'video', 'videos'))
        tags = self._html_search_meta('keywords', webpage) or ''
        tags = [t for t in re.split(r'\s*,\s*', tags) if t not in ignore_tags]

        mobj = re.search(
            r'''(?sx)
                (?P<ul><a\b[^>]+\bclass\s*=\s*["'](?:[\w-]+\s+)*uploader-tag(?:\s+[\w-]+)*[^>]+>)
                \s*<span\s+class\s*=\s*["']name\b[^>]+>\s*(?P<name>.+?)\s*<
            ''', webpage)
        creator = None
        uploader_url = None
        if mobj:
            uploader_url = urljoin(url, extract_attributes(mobj.group('ul')).get('href'))
            creator = mobj.group('name')

        def get_actor_data(mobj):
            ul_url = extract_attributes(mobj.group('ul')).get('href')
            if '/pornstars/' in ul_url:
                return {
                    'given_name': mobj.group('name'),
                    'url': urljoin(url, ul_url),
                }

        actors = traverse_obj(re.finditer(
            r'''(?sx)
                (?P<ul><a\b[^>]+\bclass\s*=\s*["'](?:[\w-]+\s+)*profile(?:\s+[\w-]+)*[^>]+>)
                \s*<span\s+class\s*=\s*["']name\b[^>]+>\s*(?P<name>.+?)\s*<
            ''', webpage), (Ellipsis, T(get_actor_data)))

        return merge_dicts({
            'id': video_id,
            'formats': formats,
            'title': title,
            'age_limit': 18,
        }, {
            'duration': duration,
            'thumbnails': thumbnails or None,
            'tags': tags or None,
            'creator': creator,
            'uploader': creator,
            'uploader_url': uploader_url,
            'cast': actors or None,
            'view_count': parse_count(get_element_by_class(
                'mobile-hide', get_element_by_id('v-views', webpage))),
            'like_count': parse_count(get_element_by_class('rating-good-nbr', webpage)),
            'dislike_count': parse_count(get_element_by_class('rating-bad-nbr', webpage)),
        }, {
            'channel': creator,
            'channel_url': uploader_url,
        } if '/channels/' in (uploader_url or '') else {})


class XVideosPlaylistBaseIE(InfoExtractor):
    def _extract_videos_from_json_list(self, json_list, path='video'):
        return traverse_obj(json_list, (
            Ellipsis, 'id', T(int_or_none),
            T(lambda x: self.url_result('https://www.xvideos.com/%s%d' % (path, x)))))

    def _get_playlist_url(self, url, playlist_id):
        """URL of first playlist page"""
        return url

    def _get_playlist_id(self, playlist_id, **kwargs):
        pnum = kwargs.get('pnum')
        return join_nonempty(playlist_id, pnum, delim='/')

    def _can_be_paginated(self, playlist_id):
        return False

    def _get_title(self, page, playlist_id, **kwargs):
        """title of playlist"""
        title = (
            self._og_search_title(page, default=None)
            or self._html_search_regex(
                r'<title\b[^>]*>([^<]+?)(?:\s+-\s+XVIDEOS\.COM)?</title>',
                page, 'title', default=None)
            or 'XVideos playlist %s' % playlist_id)
        pnum = kwargs.get('pnum')
        pnum = ('p%s' % pnum) if pnum is not None else (
            'all' if self._can_be_paginated(playlist_id) else None)
        if pnum:
            title = '%s (%s)' % (title, pnum)
        return title

    def _get_description(self, page, playlist_id):
        return None

    def _get_next_page(self, url, num, page):
        '''URL of num'th continuation page of url'''
        if page.startswith('{'):
            url, sub = re.subn(r'(/)(\d{1,7})($|[#?/])', r'\g<1>%d\3' % (num, ), url)
            if sub == 0:
                url += '/%d' % num
            return url
        return traverse_obj(
            self._search_regex(
                r'''(?s)(<a\s[^>]*?\bclass\s*=\s*(?P<q>'|")[^>]*?\bnext-page\b.*?(?P=q)[^>]*>)''',
                page, 'next page', default=None),
            (T(extract_attributes), 'href', T(lambda u: urljoin(url, u)))) or False

    def _extract_videos(self, url, playlist_id, num, page):
        """Get iterable video entries plus stop flag"""
        return (
            traverse_obj(
                re.finditer(
                    r'''<div\s[^>]*?id\s*=\s*(\'|")video_(?P<video_id>[0-9]+)\1''', page),
                (Ellipsis, 'video_id', T(lambda x: self.url_result('xvideos:' + x, ie=XVideosIE.ie_key())))),
            None)

    def _real_extract(self, url):
        mobj = self._match_valid_url(url)
        playlist_id = mobj.group('id')
        pnum = mobj.groupdict().get('pnum')
        webpage = self._download_webpage(url, playlist_id, fatal=False) or ''
        next_page = self._get_playlist_url(url, playlist_id)
        playlist_id = self._get_playlist_id(playlist_id, pnum=pnum, url=url)

        def entries(url, webpage):
            next_page = url
            ids = set()
            for count in itertools.count(0):
                if not webpage:
                    webpage = self._download_webpage(
                        next_page,
                        '%s (+%d)' % (playlist_id, count) if count > 0 else playlist_id)

                vids, stop = self._extract_videos(next_page, playlist_id, count, webpage)

                for from_ in vids:
                    h_id = hash(from_['url'])
                    if h_id not in ids:
                        yield from_
                        ids.add(h_id)

                if stop or pnum is not None:
                    break
                next_page = self._get_next_page(next_page, count + 1, webpage)
                if not next_page:
                    break
                webpage = None

        playlist_title = self._get_title(webpage, playlist_id, pnum=pnum)
        # text may have a final + as an expand widget
        description = remove_end(self._get_description(webpage, playlist_id), '+')

        return merge_dicts(self.playlist_result(
            LazyList(entries(next_page, webpage if next_page == url else None)),
            playlist_id, playlist_title), {
                'description': description,
        })


class XVideosRelatedIE(XVideosPlaylistBaseIE):
    IE_DESC = 'Related videos/playlists in the respective tabs of a video page'
    _VALID_URL = XVideosIE._VALID_URL + r'(?:/[^/]+)*?\#_related-(?P<related>videos|playlists)'

    _TESTS = [{
        'url': 'https://www.xvideos.com/video46474569/solo_girl#_related-videos',
        'info_dict': {
            'id': '46474569/related/videos',
            'title': 'solo girl (related videos)',
        },
        'playlist_mincount': 40,
    }, {
        'url': 'https://www.xvideos.com/video46474569/solo_girl#_related-playlists',
        'info_dict': {
            'id': '46474569/related/playlists',
            'title': 'solo girl (related playlists)',
        },
        'playlist_mincount': 20,
    }]

    def _get_playlist_id(self, playlist_id, **kwargs):
        url = kwargs.get('url')
        return '/related/'.join((
            playlist_id,
            self._match_valid_url(url).group('related')))

    def _get_title(self, page, playlist_id, **kwargs):
        return '%s (%s)' % (
            super(XVideosRelatedIE, self)._get_title(page, playlist_id),
            playlist_id.split('/', 1)[-1].replace('/', ' '))

    def _extract_videos(self, url, playlist_id, num, page):
        related = playlist_id.rsplit('/', 1)[-1]
        if not related:
            return super(XVideosRelatedIE, self)._extract_videos(url, playlist_id, num, page)

        if related == 'videos':
            related_json = self._search_regex(
                r'(?s)videos?_related\s*=\s*(\[.*?])\s*;',
                page, 'related', default='[]')
            related_json = self._parse_json(related_json, playlist_id, fatal=False) or []
            return (self._extract_videos_from_json_list(related_json), True)
        # playlists
        related_json = self._download_json(
            'https://www.xvideos.com/video-playlists/' + playlist_id.split('/', 1)[0], playlist_id, fatal=False)
        return (
            self._extract_videos_from_json_list(
                traverse_obj(related_json, ('playlists', Ellipsis)),
                path='favorite/'),
            True)


class XVideosPlaylistIE(XVideosPlaylistBaseIE):
    _VALID_URL = r'''(?x)
                    https?://
                        (?:[^/]+\.)?xvideos\d*\.com/
                          (?P<id>gay|shemale|best(?:/\d{4}-\d{2})|(?P<fav>favorite)/\d+)(?:(?(fav)[\w-]+/|)/(?P<pnum>\d+))?
                  '''
    _TESTS = [{
        'url': 'https://www.xvideos.com/best/2023-08/4',
        'info_dict': {
            'id': 'best/2023-08/4',
            'title': 'Playlist best (2023-08, p4)',
        },
        'playlist_count': 27,
    }, {
        'url': 'https://www.xvideos.com/favorite/84800989/mental_health',
        'info_dict': {
            'id': 'favorite/84800989',
            'title': 'Playlist favorite/84800989',
        },
        'playlist_count': 5,
    }]

    def _can_be_paginated(self, playlist_id):
        return True

    def _get_playlist_url(self, url, playlist_id):
        if url.endswith(playlist_id):
            url += '/0'
        return super(XVideosPlaylistIE, self)._get_playlist_url(url, playlist_id)

    def _get_title(self, page, playlist_id, **kwargs):
        pl_id = playlist_id.split('/')
        if pl_id[0] == 'favorite':
            pl_id[0] = '/'.join(pl_id[:2])
            del pl_id[1]
        pnum = int_or_none(pl_id[-1])
        if pnum is not None:
            pl_id[-1] = ' p%d' % pnum
        title = 'Playlist ' + pl_id[0]
        if len(pl_id) > 1:
            title = '%s (%s)' % (title, ','.join(pl_id[1:]))
        return title


class XVideosChannelIE(XVideosPlaylistIE):
    _VALID_URL = r'''(?x)
                    https?://
                        (?:[^/]+\.)?xvideos2?\.com/
                          (?:
                             (?:amateur-|pornstar-|model-)?channel|
                             pornstar
                          )s/
                            (?P<id>[^#?/]+)
                              (?:\#_tab(?P<tab>Videos|Favorites|Playlists|AboutMe)(?:,(?P<sort>[^,]+))?)?
                 '''
    _TESTS = [{
        'url': 'https://www.xvideos.com/pornstar-channels/sienna-west',
        'playlist_mincount': 5,
    }, ]

    def _get_playlist_url(self, url, playlist_id):
        webpage = self._download_webpage(url, playlist_id)
        id_match = re.match(self._VALID_URL, url).groupdict()
        tab = (id_match.get('tab') or '').lower()
        if tab:
            if tab in ('videos', 'favorites'):
                url, frag = compat_urlparse.urldefrag(url)
                if not url.endswith('/'):
                    url += '/'
                frag = frag.split(',')
                url += tab
                if tab == 'videos':
                    url += '/' + (frag[1] if len(frag) > 1 else 'best')
                url += '/0'
            return url

        # activity
        conf = self._search_regex(
            r'(?s)\.\s*xv\s*\.\s*conf\s*=\s*(\{.*?})[\s;]*</script',
            webpage, 'XV conf')
        conf = self._parse_json(conf, playlist_id)
        act = try_get(conf,
                      ((lambda x: x['dyn'][y])
                       for y in ('page_main_cat', 'user_main_cat')),
                      compat_str) or 'straight'

        url, _ = compat_urlparse.urldefrag(url)
        if url.endswith('/'):
            url = url[:-1]

        return '%s/activity/%s' % (url, act, )

    def _get_next_page(self, url, num, page):
        if page.startswith('{') or '#_tab' in url:
            return super(XVideosChannelIE, self)._get_next_page(url, num, page)

        act_time = int_or_none(url_basename(url)) or 0
        last_act = int(self._search_regex(
            r'(?s)id\s*=\s*"?activity-event-(\d{10})(?!.*id\s*=\s*"?activity-event-\d+.*).+$',
            page, 'last activity', default=act_time))
        if last_act == act_time:
            return False
        return (
            url.replace('/%d' % (act_time, ), '/%d' % (last_act, ))
            if act_time
            else url + ('/%d' % (last_act, )))

    def _extract_videos(self, url, playlist_id, num, page):
        tab = next((x for x in ('videos', 'favorites') if '/%s/' % (x, ) in url), None)
        if tab == 'videos':
            tab_json = self._parse_json(page, playlist_id, fatal=False) or {}
            more = try_get(tab_json, lambda x: x['current_page'] + 1, int)
            more = int_or_none(more, scale=tab_json.get('nb_videos'), invscale=tab_json.get('nb_per_page'), default=0)
            return (
                self._extract_videos_from_json_list(
                    try_get(tab_json, lambda x: x['videos'], list) or []),
                more > 0)

        if tab == 'favorites':
            return ((
                'https://www.xvideos.com' + x.group('playlist')
                for x in re.finditer(r'''<a\s[^>]*?href\s*=\s*('|")(?P<playlist>/favorite/\d+/[^#?]+?)\1''', page)),
                None)

        return super(XVideosChannelIE, self)._extract_videos(url, playlist_id, num, page)


class XVideosSearchIE(XVideosPlaylistIE):
    _VALID_URL = r'''(?x)
                    https?://
                        (?:[^/]+\.)?xvideos2?\.com/
                          \?k=(?P<id>[^#?/&]+)
                 '''
    _TESTS = [{
        # uninteresting search with probably at least two pages of results,
        # but not too many more
        'url': 'http://www.xvideos.com/?k=libya&sort=length',
        'playlist_mincount': 30,
    }, ]

    def _get_next_page(self, url, num, page):
        parsed_url = compat_urlparse.urlparse(url)
        qs = compat_parse_qs(parsed_url.query)
        qs['p'] = [num]
        parsed_url = (
            list(parsed_url[:4])
            + [compat_urllib_parse_urlencode(qs, True), None])
        return compat_urlparse.urlunparse(parsed_url), False
