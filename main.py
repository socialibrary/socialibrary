#!/usr/bin/env python
# coding: utf-8
# Copyright 2011 Facebook, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.


import os
# dummy config to enable registering django template filters
os.environ[u'DJANGO_SETTINGS_MODULE'] = u'conf'

from google.appengine.dist import use_library
use_library('django', '1.2')

from django.template.defaultfilters import register
from django.utils import simplejson as json
from functools import wraps
from google.appengine.api import urlfetch, taskqueue, rdbms
from google.appengine.ext import db, webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext.webapp import util, template
from google.appengine.runtime import DeadlineExceededError
from random import randrange
from uuid import uuid4
import Cookie
import base64
import cgi
import conf
import datetime
import hashlib
import hmac
import logging
import time
import traceback
import urllib
import simplejson
from google.appengine.api import urlfetch

import locale
import urllib2

_INSTANCE_NAME = 'socialibrary-db:socialibrary'

##facebook location database
#http://facebook.com/ajax/typeahead/search.php?__a=1&value=che&category&filter[0]=page&page_categories[0]=2404&context=hubs_location&services_mask=1&viewer=517160144

def htmlescape(text):
    """Escape text for use as HTML"""
    return cgi.escape(
        text, True).replace("'", '&#39;').encode('ascii', 'xmlcharrefreplace')

@register.filter(name=u'get_name')
def get_name(dic, index):
    """Django template filter to render name"""
    return dic[index].name

@register.filter(name=u'get_picture')
def get_picture(dic, index):
    """Django template filter to render picture"""
    return dic[index].picture

def select_random(lst, limit):
    """Select a limited set of random non Falsy values from a list"""
    final = []
    size = len(lst)
    while limit and size:
        index = randrange(min(limit, size))
        size = size - 1
        elem = lst[index]
        lst[index] = lst[size]
        if elem:
            limit = limit - 1
            final.append(elem)
    return final

_USER_FIELDS = u'name,email,picture,friends,location'
class User(db.Model):
    user_id = db.StringProperty(required=True)
    access_token = db.StringProperty(required=True)
    name = db.StringProperty(required=True)
    picture = db.StringProperty(required=True)
    email = db.StringProperty()
    locationid = db.StringProperty()
    locationname = db.StringProperty()
    friends = db.StringListProperty()
    dirty = db.BooleanProperty()
    location_entered = db.BooleanProperty(default = False)

    def refresh_data(self):
        """Refresh this user's data using the Facebook Graph API"""
        me = Facebook().api(u'/me',
            {u'fields': _USER_FIELDS, u'access_token': self.access_token})
        self.dirty = False
        self.name = me[u'name']
        self.email = me.get(u'email')
        self.picture = me[u'picture']

        loc = me.get(u'location')
        logging.info(loc)
        if loc != None:
            self.locationid = loc['id']
            self.locationname = loc['name']
        else:
            self.locationid = "No place entered"
            self.locationname = "No place entered"

        self.friends = [user[u'id'] for user in me[u'friends'][u'data']]
        return self.put()

class Book(db.Model):
    title = db.StringProperty(required=True)
    normalised_title = db.StringProperty(required=True)
    author = db.StringProperty(required=True)
    rating = db.IntegerProperty()
    usercount = db.IntegerProperty()
    createdby = db.StringProperty()
    lastupdated = db.DateTimeProperty()

class Game(db.Model):
    title = db.StringProperty(required=True)
    normalised_title = db.StringProperty(required=True)
    platform = db.StringProperty()
    pageurl = db.StringProperty()
    rating = db.IntegerProperty()
    usercount = db.IntegerProperty()
    createdby = db.StringProperty()
    lastupdated = db.DateTimeProperty()

class Movie(db.Model):
    title = db.StringProperty(required=True)
    normalised_title = db.StringProperty(required=True)
    actors = db.StringProperty()
    pageurl = db.StringProperty()
    rating = db.IntegerProperty()
    usercount = db.IntegerProperty()
    createdby = db.StringProperty()
    lastupdated = db.DateTimeProperty()

class Fact(db.Model):
    userID=db.StringProperty()
    userName=db.StringProperty()
    category=db.IntegerProperty()
    itemID=db.IntegerProperty()
    location=db.StringProperty()
    itemName=db.StringProperty()

class RunException(Exception):
    pass

class FacebookApiError(Exception):
    def __init__(self, result):
        self.result = result

    def __str__(self):
        return self.__class__.__name__ + ': ' + json.dumps(self.result)

class Facebook(object):
    """Wraps the Facebook specific logic"""
    def __init__(self, app_id=conf.FACEBOOK_APP_ID,
            app_secret=conf.FACEBOOK_APP_SECRET):
        self.app_id = app_id
        self.app_secret = app_secret
        self.user_id = None
        self.access_token = None
        self.signed_request = {}

    def api(self, path, params=None, method=u'GET', domain=u'graph'):
        """Make API calls"""
        if not params:
            params = {}
        params[u'method'] = method
        if u'access_token' not in params and self.access_token:
            params[u'access_token'] = self.access_token
        result = json.loads(urlfetch.fetch(
            url=u'https://' + domain + u'.facebook.com' + path,
            payload=urllib.urlencode(params),
            method=urlfetch.POST,
            headers={
                u'Content-Type': u'application/x-www-form-urlencoded'})
            .content)
        if isinstance(result, dict) and u'error' in result:
            raise FacebookApiError(result)
        return result

    def load_signed_request(self, signed_request):
        """Load the user state from a signed_request value"""
        try:
            sig, payload = signed_request.split(u'.', 1)
            sig = self.base64_url_decode(sig)
            data = json.loads(self.base64_url_decode(payload))

            expected_sig = hmac.new(
                self.app_secret, msg=payload, digestmod=hashlib.sha256).digest()

            # allow the signed_request to function for upto 1 day
            if sig == expected_sig and \
                    data[u'issued_at'] > (time.time() - 86400):
                self.signed_request = data
                self.user_id = data.get(u'user_id')
                self.access_token = data.get(u'oauth_token')
        except ValueError, ex:
            pass # ignore if can't split on dot

    @property
    def user_cookie(self):
        """Generate a signed_request value based on current state"""
        if not self.user_id:
            return
        payload = self.base64_url_encode(json.dumps({
            u'user_id': self.user_id,
            u'issued_at': str(int(time.time())),
        }))
        sig = self.base64_url_encode(hmac.new(
            self.app_secret, msg=payload, digestmod=hashlib.sha256).digest())
        return sig + '.' + payload

    @staticmethod
    def base64_url_decode(data):
        data = data.encode(u'ascii')
        data += '=' * (4 - (len(data) % 4))
        return base64.urlsafe_b64decode(data)

    @staticmethod
    def base64_url_encode(data):
        return base64.urlsafe_b64encode(data).rstrip('=')

class CsrfException(Exception):
    pass

class BaseHandler(webapp.RequestHandler):
    facebook = None
    user = None
    csrf_protect = True

    def initialize(self, request, response):
        """General initialization for every request"""
        super(BaseHandler, self).initialize(request, response)

        try:
            self.init_facebook()
            self.init_csrf()
            self.response.headers[u'P3P'] = u'CP=HONK'  # iframe cookies in IE
        except Exception, ex:
            self.log_exception(ex)
            raise

    def handle_exception(self, ex, debug_mode):
        """Invoked for unhandled exceptions by webapp"""
        self.log_exception(ex)
        self.render(u'error',
            trace=traceback.format_exc(), debug_mode=debug_mode)

    def log_exception(self, ex):
        """Internal logging handler to reduce some App Engine noise in errors"""
        msg = ((str(ex) or ex.__class__.__name__) +
                u': \n' + traceback.format_exc())
        if isinstance(ex, urlfetch.DownloadError) or \
           isinstance(ex, DeadlineExceededError) or \
           isinstance(ex, CsrfException) or \
           isinstance(ex, taskqueue.TransientError):
            logging.warn(msg)
        else:
            logging.error(msg)

    def set_cookie(self, name, value, expires=None):
        """Set a cookie"""
        if value is None:
            value = 'deleted'
            expires = datetime.timedelta(minutes=-50000)
        jar = Cookie.SimpleCookie()
        jar[name] = value
        jar[name]['path'] = u'/'
        if expires:
            if isinstance(expires, datetime.timedelta):
                expires = datetime.datetime.now() + expires
            if isinstance(expires, datetime.datetime):
                expires = expires.strftime('%a, %d %b %Y %H:%M:%S')
            jar[name]['expires'] = expires
        self.response.headers.add_header(*jar.output().split(u': ', 1))

    def render(self, name, **data):
        """Render a template"""
        if not data:
            data = {}
        data[u'js_conf'] = json.dumps({
            u'appId': conf.FACEBOOK_APP_ID,
            u'canvasName': conf.FACEBOOK_CANVAS_NAME,
            u'userIdOnServer': self.user.user_id if self.user else None,
        })
        data[u'logged_in_user'] = self.user
        data[u'message'] = self.get_message()
        data[u'csrf_token'] = self.csrf_token
        data[u'canvas_name'] = conf.FACEBOOK_CANVAS_NAME


        self.response.out.write(template.render(
            os.path.join(
                os.path.dirname(__file__), 'templates', name + '.html'),
            data))

    def init_facebook(self):
        """Sets up the request specific Facebook and User instance"""
        facebook = Facebook()
        user = None

        # initial facebook request comes in as a POST with a signed_request
        if u'signed_request' in self.request.POST:
            facebook.load_signed_request(self.request.get('signed_request'))
            # we reset the method to GET because a request from facebook with a
            # signed_request uses POST for security reasons, despite it
            # actually being a GET. in webapp causes loss of request.POST data.
            self.request.method = u'GET'
            self.set_cookie(
                'u', facebook.user_cookie, datetime.timedelta(minutes=1440))
        elif 'u' in self.request.cookies:
            facebook.load_signed_request(self.request.cookies.get('u'))

        # try to load or create a user object
        if facebook.user_id:
            user = User.get_by_key_name(facebook.user_id)
            if user:
                # update stored access_token
                if facebook.access_token and \
                        facebook.access_token != user.access_token:
                    user.access_token = facebook.access_token
                    user.put()
                # refresh data if we failed in doing so after a realtime ping
                if user.dirty:
                    user.refresh_data()
                # restore stored access_token if necessary
                if not facebook.access_token:
                    facebook.access_token = user.access_token

            if not user and facebook.access_token:
                me = facebook.api(u'/me', {u'fields': _USER_FIELDS})
                try:
                    friends = [user[u'id'] for user in me[u'friends'][u'data']]
                    user = User(key_name=facebook.user_id,
                        user_id=facebook.user_id, friends=friends,
                        access_token=facebook.access_token, name=me[u'name'],
                        email=me.get(u'email'), picture=me[u'picture'])
                    user.put()
                except KeyError, ex:
                    pass # ignore if can't get the minimum fields

        self.facebook = facebook
        self.user = user

    def init_csrf(self):
        """Issue and handle CSRF token as necessary"""
        self.csrf_token = self.request.cookies.get(u'c')
        if not self.csrf_token:
            self.csrf_token = str(uuid4())[:8]
            self.set_cookie('c', self.csrf_token)
        if self.request.method == u'POST' and self.csrf_protect and \
                self.csrf_token != self.request.POST.get(u'_csrf_token'):
            raise CsrfException(u'Missing or invalid CSRF token.')

    def set_message(self, **obj):
        """Simple message support"""
        self.set_cookie('m', base64.b64encode(json.dumps(obj)) if obj else None)

    def get_message(self):
        """Get and clear the current message"""
        message = self.request.cookies.get(u'm')
        if message:
            self.set_message()  # clear the current cookie
            return json.loads(base64.b64decode(message))


def user_required(fn):
    """Decorator to ensure a user is present"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        handler = args[0]
        if handler.user:
            return fn(*args, **kwargs)
        handler.redirect(u'/')
    return wrapper


class WelcomeHandler(BaseHandler):
    """Show recent runs for the user and friends"""
    def get(self):
	    if self.user:
                conn = rdbms.connect(instance=_INSTANCE_NAME, database='socialibrary')
                cursor = conn.cursor()
                ##TODO: Add a condition in templates for no books
                """Getting list of Books
                """

                cursor.execute('SELECT * FROM owner_book_mapping WHERE user_id = %s',(self.user.user_id))
                book_rows = cursor.fetchall()
                books= []
                for book in book_rows:
                    book_item_id = book[1]
                    cursor.execute('SELECT title FROM books WHERE entryID = %s', book_item_id)
                    b = cursor.fetchone()
                    books.append(b[0])
                """Getting list of Movies
                """
                cursor.execute('SELECT * FROM owner_movie_mapping WHERE user_id = %s',(self.user.user_id))
                movie_rows = cursor.fetchall()
                movies = []
                for movie in movie_rows:
                    movie_item_id = movie[2]
                    cursor.execute('SELECT title FROM movies WHERE entryID = %s', movie_item_id)
                    m = cursor.fetchone()
                    movies.append(m[0])
                """Getting list of Games
                """
                cursor.execute('SELECT * FROM owner_game_mapping WHERE user_id = %s',(self.user.user_id))
                game_rows = cursor.fetchall()
                games = []
                for game in game_rows:
                    game_item_id = game[2]
                    cursor.execute('SELECT title FROM games WHERE entryID = %s', game_item_id)
                    g = cursor.fetchone()
                    games.append(g[0])

                conn.commit()
                conn.close()
                if self.request.get("ref") == "bookmarks":
                    self.render(u'bookmark_landing')
                else:
                    self.render(u'userstart',books=books,movies=movies,games=games)
            else:
            	self.render(u'welcome')

class SearchItemHandler(BaseHandler):
    """ Search action for game, action or book """
    # def getfacts(self, entityresultset, category, tmpcnt):
    #     result = set()
    #     cnt = tmpcnt
    #     for entity in entityresultset:
    #         if cnt >= 100:
    #             break
    #         query = Fact.gql("WHERE itemID = :1 AND category = :2 AND location=:3", entity.key().id(), category, self.user.locationname)
    #         sameloc = query.fetch(100)
    #         for tmpfact in sameloc:
    #             if cnt >= 100:
    #                 break
    #             cnt=cnt+1
    #             result.add(tmpfact)
    #         if cnt >= 100:
    #             break
    #         query = Fact.gql("WHERE itemID = :1 AND category = :2 AND location != :3", entity.key().id(), category, self.user.locationname)
    #         diffloc = query.fetch(100)
    #         for tmpfact in diffloc:
    #             if cnt > 100:
    #                 break
    #             cnt=cnt+1
    #             result.add(tmpfact)
    #     return result, cnt

    def get(self):
        if self.user:
            conn = rdbms.connect(instance=_INSTANCE_NAME, database='socialibrary')
            cursor = conn.cursor()

            """ web response to search query """
            search_key = self.request.get("search_key")
            category = int(self.request.get("category"))
            result = set()
            #location = self.user.locationid #Production
            location = 'bangalore' # test
            if category == 3:
                users = []
                cursor.execute('SELECT * FROM owner_game_mapping WHERE game_entry_id = %s AND user_location = %s',(search_key, location))

                mappings = cursor.fetchall()
                for mapping in mappings:
                    user_query = User.gql("WHERE user_id = :1", mapping[1])
                    user = user_query.fetch(1)
                    cursor.execute('SELECT title FROM games WHERE entryID = %s',(mapping[2]))
                    title = cursor.fetchone()
                    users.append({'item_name': title[0], 'user_name': user[0].name})

            elif category == 2:
                users = []
                cursor.execute('SELECT * FROM owner_movie_mapping WHERE movie_entry_id = %s AND user_location = %s',(search_key, location))
                mappings = cursor.fetchall()
                for mapping in mappings:
                    user_query = User.gql("WHERE user_id = :1", mapping[1])
                    user = user_query.fetch(1)
                    cursor.execute('SELECT title FROM movies WHERE entryID = %s',(mapping[2]))
                    title = cursor.fetchone()
                    users.append({'item_name': title[0], 'user_name': user[0].name})


            elif category == 1:
                users = []
                cursor.execute('SELECT * FROM owner_book_mapping WHERE book_entry_id = %s AND user_location = %s',(search_key, location))

                mappings = cursor.fetchall()
                for mapping in mappings:
                    user_query = User.gql("WHERE user_id = :1", mapping[2])
                    user = user_query.fetch(1)
                    cursor.execute('SELECT title FROM books WHERE entryID = %s',(mapping[1]))
                    title = cursor.fetchone()
                    users.append({'item_name': title[0], 'user_name': user[0].name})


            template_values = {'users' : users, 'category': category}
            self.response.out.write(template.render(
                    os.path.join(
                        os.path.dirname(__file__), 'templates', 'searcheditem.html'),
                    template_values))
            conn.commit()
            conn.close()
        else:
            self.render(u'welcome')

class SearchHandler(BaseHandler):
    """ Search action for game, action or book """
    def get(self):
        if self.user:
            """ web response to search query """
            if not self.user.location_entered:
                if self.user.locationid and not self.user.locationid == "No place entered":
                    conn = rdbms.connect(instance=_INSTANCE_NAME, database='socialibrary')
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO user_info (user_id, user_location, user_rating) values (%s, %s, %s)",
                                  (self.user.user_id, self.user.locationid, 2.5))
                else:
                    self.render(u'search', get_location=True, user_id=self.user.user_id)
            else:
                self.render(u'search', get_location=False)
            #This is code to update the counter of the app.
            #url='https://api.facebook.com/method/dashboard.setCount?count=30&uid='+self.user.user_id+'&access_token='+self.user.access_token+'&format=json'
            #urllib.urlopen(url)
        else:
            self.render(u'welcome')

class AddHandler(BaseHandler):
    """ Search action for game, action or book """
    def get(self):
        if self.user:
            """ web response to search query """
            self.render(u'addentity')
        else:
            self.render(u'welcome')

class UpdateLocation(BaseHandler):
    def get(self):
        if self.user:
            """ web response to search query """
            location_id = self.request.get("location")
            self.user.location_entered = True
            conn = rdbms.connect(instance=_INSTANCE_NAME, database='socialibrary')
            cursor = conn.cursor()
            cursor.execute("INSERT INTO user_info (user_id, user_location) values(%s, %s)",(self.user.user_id, location_id))
            conn.commit()
            conn.close()
            status ={'success':True}
            self.response.out.write(json.dumps(status))
        else:
            self.response.out.write("Login Buddy")

class PlacesHandler(BaseHandler):
    def get(self):
        if self.user:
            """ web response to search query """
            searchtext = self.request.get("search_term")

            list = []
            url = 'https://maps.googleapis.com/maps/api/place/autocomplete/json?input=%s&sensor=false&key=AIzaSyDR743Q6Dt6mKyGSKybFxDEqMgwFI-MX5U'%searchtext
            try:
              result = simplejson.load(urllib.urlopen(url))

              for value in result['predictions']:

                 if 'locality' in value['types']:
                    list.append({"label":value['description'],"value":value['description'], "key":value['id']})
            except urllib2.URLError, e:
              handleError(e)

            self.response.out.write(json.dumps(list))
        else:
            self.response.out.write("Login Buddy")

class AutoCompleteHandler(BaseHandler):
    def get(self):
        if self.user:
            """ web response to search query """
            conn = rdbms.connect(instance=_INSTANCE_NAME, database='socialibrary')
            cursor = conn.cursor()
            searchtext = self.request.get("search_term")
            cleaned_searchtext = "%"+searchtext.replace("%","%%")+"%"
            category = int(self.request.get("category"))
            result = set()
            if category == 3:
                cursor.execute('SELECT * FROM games WHERE title LIKE %s',(cleaned_searchtext))
                games =  cursor.fetchall()
                list = []
                unique_game_titles = {}
                for game in games:
                    if game[0] not in unique_game_titles:
                        unique_game_titles[game[0]] = True
                        list.append({"label":game[0], "value":game[0], "key":game[3]})
                self.response.out.write(json.dumps(list))

            elif category == 2:
                cursor.execute('SELECT * FROM movies WHERE title LIKE %s',(cleaned_searchtext))
                movies =  cursor.fetchall()
                list = []
                unique_movie_titles = {}
                for movie in movies:
                    if movie[0] not in unique_movie_titles:
                        unique_movie_titles[movie[0]] = True
                        list.append({"label":movie[0], "value":movie[0], "key":movie[4]})

                self.response.out.write(json.dumps(list))

            elif category == 1:

                cursor.execute('SELECT * FROM books WHERE title LIKE %s',(cleaned_searchtext))
                books =  cursor.fetchall()
                list = []
                unique_book_titles = {}
                for book in books:
                    if book[0] not in unique_book_titles:
                        unique_book_titles[book[0]] = True
                        list.append({"label":book[0], "value":book[0], "key":book[3]})

                self.response.out.write(json.dumps(list))
            conn.commit()
            conn.close()
        else:
            self.response.out.write(u'Login buddy')

class AddItemHandler(BaseHandler):
    """ Search action for game, action or book """
    def get(self):
        if self.user:
            """ web response to search query """
            category = int(self.request.get("category"))

            conn = rdbms.connect(instance=_INSTANCE_NAME, database='socialibrary')
            cursor = conn.cursor()
            #location = self.user.locationid #Production
            location = 'bangalore' # test
            if category == 1:
                author = self.request.get("author")
                title = self.request.get("title")
                rating = int(self.request.get("rating"))
                lastupdated = datetime.datetime.now()
                created_by = self.user.user_id
                cursor.execute('INSERT INTO books (title, author, rating, created_by) VALUES (%s, %s, %s, %s)', (title, author, rating, created_by))
                cursor.execute('INSERT INTO owner_book_mapping(user_id, book_entry_id) VALUES (%s, %s)',(created_by, cursor.lastrowid))
            elif category == 2:
                actor = self.request.get("actor")
                #director = self.request.get("director")
                title = self.request.get("title")
                genre = self.request.get("genre")
                rating = int(self.request.get("rating"))
                lastupdated = datetime.datetime.now()
                created_by = self.user.user_id
                cursor.execute('INSERT INTO movies (title, actor, rating, genre, created_by) VALUES (%s, %s, %s, %s, %s)', (title, actor, rating, genre, created_by))
                cursor.execute('INSERT INTO owner_movie_mapping(user_id, movie_entry_id) VALUES (%s, %s)',(created_by, cursor.lastrowid))
            elif category == 3:
                title = self.request.get("title")
                platform = self.request.get("platform")
                rating = int(self.request.get("rating"))
                created_by = self.user.user_id
                lastupdated = datetime.datetime.now()
                cursor.execute('INSERT INTO games (title, platform, rating, created_by) VALUES (%s, %s, %s, %s)', (title, platform, rating, created_by))
                cursor.execute('INSERT INTO owner_game_mapping(user_id, game_entry_id) VALUES (%s, %s)',(created_by, cursor.lastrowid))
            conn.commit()
            conn.close()
            template_values = {'titletext': self.request.get("title")}
            self.response.out.write(template.render(
                    os.path.join(
                        os.path.dirname(__file__), 'templates', 'addeditem.html'),
                    template_values))
        else:
            self.render(u'welcome')

def main():
    routes = [
        (r'/', WelcomeHandler),
        (r'/search', SearchHandler),
        (r'/searchitem', SearchItemHandler),
        (r'/add', AddHandler),
        (r'/additem', AddItemHandler),
        (r'/search_ac', AutoCompleteHandler),
        (r'/search_places', PlacesHandler),
        (r'/update_location', UpdateLocation),
    ]
    application = webapp.WSGIApplication(routes,
        debug=os.environ.get('SERVER_SOFTWARE', '').startswith('Dev'))
    util.run_wsgi_app(application)


if __name__ == u'__main__':
    main()
