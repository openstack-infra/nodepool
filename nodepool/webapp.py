# Copyright 2012 Hewlett-Packard Development Company, L.P.
# Copyright 2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import threading
import time
from paste import httpserver
import webob
from webob import dec

import status

"""Nodepool main web app.

Nodepool supports HTTP requests directly against it for determining
status. These responses are provided as preformatted text for now, but
should be augmented or replaced with JSON data structures.
"""


class Cache(object):
    def __init__(self, expiry=1):
        self.cache = {}
        self.expiry = expiry

    def get(self, key):
        now = time.time()
        if key in self.cache:
            lm, value = self.cache[key]
            if now > lm + self.expiry:
                del self.cache[key]
                return None
            return (lm, value)

    def put(self, key, value):
        now = time.time()
        res = (now, value)
        self.cache[key] = res
        return res


class WebApp(threading.Thread):
    log = logging.getLogger("nodepool.WebApp")

    def __init__(self, nodepool, port=8005, cache_expiry=1):
        threading.Thread.__init__(self)
        self.nodepool = nodepool
        self.port = port
        self.cache = Cache(cache_expiry)
        self.cache_expiry = cache_expiry
        self.daemon = True
        self.server = httpserver.serve(dec.wsgify(self.app), host='0.0.0.0',
                                       port=self.port, start_loop=False)

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.server_close()

    def get_cache(self, path):
        result = self.cache.get(path)
        if result:
            return result
        if path == '/image-list':
            table = status.image_list(self.nodepool.getZK())
        elif path == '/dib-image-list':
            table = status.dib_image_list(self.nodepool.getZK())
        else:
            return None
        return self.cache.put(path, table)

    def app(self, request):
        result = self.get_cache(request.path)
        if result is None:
            raise webob.exc.HTTPNotFound()
        last_modified, table = result

        response = webob.Response(body=table,
                                  content_type='text/plain')
        response.headers['Access-Control-Allow-Origin'] = '*'

        response.cache_control.public = True
        response.cache_control.max_age = self.cache_expiry
        response.last_modified = last_modified
        response.expires = last_modified + self.cache_expiry

        return response.conditional_response_app
