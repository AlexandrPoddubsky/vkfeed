# -*- coding: utf-8 -*-

'''Reads a wall of the specified user using VKontakte API.'''

import json
import datetime
import logging
import re
import urllib

import vkfeed.util
from vkfeed import constants
from vkfeed.core import Error

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)


_TEXT_URL_RE = re.compile(r'(^|\s|>)(https?://[^"]+?)(\.?(?:<|\s|$))')
'''Matches a URL in a plain text.'''

_DOMAIN_ONLY_TEXT_URL_RE = re.compile(r'(^|\s|>)((?:[a-z0-9](?:[-a-z0-9]*[a-z0-9])?\.)+[a-z0-9](?:[-a-z0-9]*[a-z0-9])/[^"]+?)(\.?(?:<|\s|$))')
'''Matches a URL without protocol specification in a plain text.'''

_USER_LINK_RE = re.compile(r'\[((?:id|club)\d+)\|([^\]]+)\]')
'''Matches a user link in a post text.'''

_GROUP_ALIAS_RE = re.compile(r'^(?:event|public)(\d+)$')
'''Matches group ID aliases.'''


class ConnectionError(Error):
    '''Raised when we fail to get a data from the server.'''

    def __init__(self, *args, **kwargs):
        Error.__init__(self, *args, **kwargs)

class ServerError(Error):
    '''Raised when the server reports an error.'''

    def __init__(self, code, *args, **kwargs):
        Error.__init__(self, *args, **kwargs)
        self.code = code


def read(profile_name, foreign_posts, show_photo):
    '''Reads a wall of the specified user.'''

    user = _get_user(profile_name)
    reply = _api('wall.get', owner_id = user['id'], extended = 1)

    users = {}

    for profile in reply.get('profiles', []):
        users[profile['uid']] = {
            'name':  profile['first_name'] + ' ' + profile['last_name'],
            'photo': profile['photo'],
        }

    for profile in reply.get('groups', []):
        users[-profile['gid']] = {
            'name':  profile['name'],
            'photo': profile['photo'],
        }

    img_style = 'style="border-style: none; display: block;"'

    posts = []
    for post in reply['wall'][1:]:
        if not foreign_posts and post['from_id'] != user['id']:
            LOG.debug(u'Ignore post %s from user %s.', post['id'], post['from_id'])
            continue

        supported = []
        unsupported = []

        if 'attachment' in post and post['text'] == post['attachment'][post['attachment']['type']].get('title'):
            post['text'] = ''

        for attachment in post.get('attachments', []):
            info = attachment[attachment['type']]

            if attachment['type'] == 'app':
                supported.append(
                    u'<a href="{vk_url}app{info[app_id]}"><img {img_style} src="{info[src]}" /></a>'.format(
                        vk_url = constants.VK_URL, info = info, img_style = img_style))
            elif attachment['type'] == 'graffiti':
                supported.append(
                    u'<a href="{vk_url}graffiti{info[gid]}"><img {img_style} src="{info[src]}" /></a>'.format(
                        vk_url = constants.VK_URL, info = info, img_style = img_style))
            elif attachment['type'] == 'link':
                info['description'] = _parse_text(info['description']) or info['title']

                html = u'<b>Ссылка: <a href="{info[url]}">{info[title]}</a></b><p>'.format(info = info)

                if info.get('image_src') and info['description']:
                    html += (
                        u'<table cellpadding="0" cellspacing="0"><tr valign="top">'
                            '<td><a href="{info[url]}"><img {img_style} src="{info[image_src]}" /></a></td>'
                            '<td style="padding-left: 10px;">{info[description]}</td>'
                        '</tr></table>'.format(info = info, img_style = img_style))
                elif info.get('image_src'):
                    html += u'<a href="{info[url]}"><img {img_style} src="{info[image_src]}" /></a>'.format(
                        info = info, img_style = img_style)
                elif info['description']:
                    html += info['description']

                html += '</p>'

                supported.append(html)
            elif attachment['type'] in ('photo', 'posted_photo'):
                photo_id = info.get('pid', info.get('id', 0))
                photo_count = reduce(
                    lambda count, attachment:
                        count + ( attachment['type'] in ('photo', 'posted_photo') ),
                    post['attachments'], 0)
                photo_src = info['src_big'] if photo_count == 1 else info['src']

                # Photo may have id = 0 and owner_id = 0 if it for example
                # generated by an application.
                if photo_id == 0 or info['owner_id'] == 0:
                    supported.append(
                        u'<a href="{vk_url}wall{profile_id}_{post_id}"><img {img_style} src="{photo_src}" /></a>'.format(
                            vk_url = constants.VK_URL, profile_id = user['id'], post_id = post['id'],
                            img_style = img_style, photo_src = photo_src))
                else:
                    supported.append(
                        u'<a href="{vk_url}wall{profile_id}_{post_id}?z=photo{info[owner_id]}_{photo_id}%2Fwall{profile_id}_{post_id}">'
                        '<img {img_style} src="{photo_src}" /></a>'.format(
                            vk_url = constants.VK_URL, profile_id = user['id'], photo_id = photo_id,
                            info = info, post_id = post['id'], img_style = img_style, photo_src = photo_src))
            elif attachment['type'] == 'video':
                supported.append(
                    u'<a href="{vk_url}video{info[owner_id]}_{info[vid]}">'
                        '<img {img_style} src="{info[image]}" />'
                        '<b>{info[title]} ({duration})</b>'
                    '</a>'.format(
                        vk_url = constants.VK_URL, info = info, img_style = img_style,
                        duration = _get_duration(info['duration'])))

            elif attachment['type'] == 'audio':
                unsupported.append(u'<b>Аудиозапись: <a href="{vk_url}search?{query}">{title}</a></b>'.format(
                    vk_url = constants.VK_URL, query = urllib.urlencode({
                        'c[q]': (info['performer'] + ' - ' + info['title']).encode('utf-8'),
                        'c[section]': 'audio'
                    }), title = u'{} - {} ({})'.format(info['performer'], info['title'], _get_duration(info['duration']))))
            elif attachment['type'] == 'doc':
                unsupported.append(u'<b>Документ: {}</b>'.format(info['title']))
            elif attachment['type'] == 'note':
                unsupported.append(u'<b>Заметка: {}</b>'.format(info['title']))
            elif attachment['type'] == 'page':
                unsupported.append(u'<b>Страница: {}</b>'.format(info['title']))
            elif attachment['type'] == 'poll':
                unsupported.append(u'<b>Опрос: {}</b>'.format(info['question']))

        text = ''

        if supported:
            text += '<p>' + '</p><p>'.join(supported) + '</p>'

        text += _parse_text(post['text'])

        if unsupported:
            text += '<p>' + '</p><p>'.join(unsupported) + '</p>'

        if 'copy_owner_id' in post and 'copy_post_id' in post:
            text = u'<p><b><a href="{profile_url}">{user_name}</a></b> пишет:</p>'.format(
                profile_url = _get_profile_url(post['copy_owner_id']), user_name = users[post['copy_owner_id']]['name']) + text

            if 'copy_text' in post:
                text = u'<p>{}</p><div style="margin-left: 1em;">{}</div>'.format(post['copy_text'], text)

        if 'reply_owner_id' in post and 'reply_post_id' in post:
            text += (
                u'<p><i>'
                    u'В ответ на <a href="{vk_url}wall{post[reply_owner_id]}_{post[reply_post_id]}">запись</a> '
                    u'пользователя <b><a href="{profile_url}">{user_name}</a></b>.'
                '</i></p>'.format(vk_url = constants.VK_URL, post = post,
                    profile_url = _get_profile_url(post['reply_owner_id']), user_name = users[post['reply_owner_id']]['name']))

        if show_photo:
            text = (
                u'<table cellpadding="0" cellspacing="0"><tr valign="top">'
                    '<td><a href="{url}"><img {img_style} src="{photo}" /></a></td>'
                    '<td style="padding-left: 10px;">{text}</td>'
                '</tr></table>'.format(
                    url = _get_profile_url(post['from_id']), img_style = img_style,
                    photo = users[post['from_id']]['photo'], text = text))

        date = (
            datetime.datetime.fromtimestamp(post['date'])
            # Take MSK timezone into account
            + datetime.timedelta(hours = 4))

        posts.append({
            'title': users[post['from_id']]['name'],
            'url':   u'{0}wall{1}_{2}'.format(constants.VK_URL, user['id'], post['id']),
            'text':  text,
            'date':  date,
        })

    return {
        'url':        constants.VK_URL + profile_name,
        'user_name':  user['name'],
        'user_photo': user['photo'],
        'posts':      posts,
    }


def _api(method, **kwargs):
    '''Calls the specified VKontakte API method.'''

    url = u'{0}method/{1}?language=0&'.format(constants.API_URL, method) + urllib.urlencode(kwargs)

    try:
        data = vkfeed.util.fetch_url(url, content_type = 'application/json')

        try:
            data = json.loads(data)
        except Exception as e:
            raise Error(u'Failed to parse JSON data: {0}.', e)
    except Exception as e:
        raise ConnectionError(u'API call {0} failed: {1}', url, e)

    if 'error' in data or 'response' not in data:
        error = data.get('error', {}).get('error_msg', '').strip()

        if not error:
            error = u'Ошибка вызова API.'
        elif error == 'Access denied: group is blocked':
            error = (
                u'Страница временно заблокирована и проверяется администраторами, '
                u'так как некоторые пользователи считают, что она не соответствует правилам сайта.')
        elif error == 'Access denied: this wall available only for community members':
            error = u'Это частное сообщество. Доступ только по приглашениям администраторов.'
        elif error == 'User was deleted or banned':
            error = u'Пользователь удален или забанен.'
        elif not error.endswith('.'):
            error += '.'

        raise ServerError(data.get('error', {}).get('error_code'), error)

    return data['response']


def _get_duration(seconds):
    '''Returns audio/video duration string.'''

    hours = seconds / 60 / 60
    minutes = seconds / 60 % 60
    seconds = seconds % 60

    if hours:
        return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)
    else:
        return '{:02d}:{:02d}'.format(minutes, seconds)


def _get_profile_url(profile_id):
    '''Returns URL to profile with the specified ID.'''

    return constants.VK_URL + ( 'club' if profile_id < 0 else 'id' ) + str(abs(profile_id))


def _get_user(profile_name):
    '''Returns user info by profile name.'''

    try:
        profile = _api('users.get', uid = profile_name, fields = 'photo_big,photo_medium,photo')[0]
        user = {
            'id':   profile['uid'],
            'name': profile['first_name'] + ' ' + profile['last_name'],
        }
    except ServerError as e:
        # Invalid user ID
        if e.code == 113:
            try:
                # VKontakte API doesn't understand group ID aliases
                match = _GROUP_ALIAS_RE.match(profile_name)
                if match is not None:
                    profile_name = 'club' + match.group(1)

                profile = _api('groups.getById', gid = profile_name, fields = 'photo_big,photo_medium,photo')[0]
                user = {
                    'id':    -profile['gid'],
                    'name':  profile['name'],
                }
            except ServerError as e:
                # Invalid group ID
                if e.code == 125:
                    raise ServerError(113, u'Пользователя не существует.')
                else:
                    raise e
        else:
            raise e

    if 'photo_big' in profile:
        user['photo'] = profile['photo_big']
    elif 'photo_medium' in profile:
        user['photo'] = profile['photo_medium']
    else:
        user['photo'] = profile['photo']

    return user


def _parse_text(text):
    '''Parses a post text.'''

    text = _TEXT_URL_RE.sub(r'\1<a href="\2">\2</a>\3', text)
    text = _DOMAIN_ONLY_TEXT_URL_RE.sub(r'\1<a href="http://\2">\2</a>\3', text)
    text = _USER_LINK_RE.sub(r'<b><a href="{}\1">\2</a></b>'.format(constants.VK_URL), text)

    return text

