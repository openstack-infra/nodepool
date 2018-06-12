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

import json
import logging
import threading
import time
from paste import httpserver
import webob
from webob import dec

from nodepool import status

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

    def __init__(self, nodepool, port=8005, listen_address='0.0.0.0',
                 cache_expiry=1):
        threading.Thread.__init__(self)
        self.nodepool = nodepool
        self.port = port
        self.listen_address = listen_address
        self.cache = Cache(cache_expiry)
        self.cache_expiry = cache_expiry
        self.daemon = True
        self.server = httpserver.serve(dec.wsgify(self.app),
                                       host=self.listen_address,
                                       port=self.port, start_loop=False)

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.server_close()

    def get_cache(self, path, params, request_type):
        # TODO quick and dirty way to take query parameters
        # into account when caching data
        if params:
            index = "%s.%s.%s" % (path,
                                  json.dumps(params.dict_of_lists(),
                                             sort_keys=True),
                                  request_type)
        else:
            index = "%s.%s" % (path, request_type)
        result = self.cache.get(index)
        if result:
            return result

        zk = self.nodepool.getZK()

        if path == '/image-list':
            results = status.image_list(zk)
        elif path == '/dib-image-list':
            results = status.dib_image_list(zk)
        elif path == '/node-list':
            results = status.node_list(zk,
                                       node_id=params.get('node_id'))
        elif path == '/request-list':
            results = status.request_list(zk)
        elif path == '/label-list':
            results = status.label_list(zk)
        else:
            return None

        fields = None
        if params.get('fields'):
            fields = params.get('fields').split(',')

        output = status.output(results, request_type, fields)
        return self.cache.put(index, output)

    def _request_wants(self, request):
        '''Find request content-type

        :param request: The incoming request
        :return str: Best guess of either 'pretty' or 'json'
        '''
        acceptable = request.accept.acceptable_offers(
            ['text/html', 'text/plain', 'application/json'])
        if acceptable[0][0] == 'application/json':
            return 'json'
        else:
            return 'pretty'

    def app(self, request):

        request_type = self._request_wants(request)
        result = self.get_cache(request.path, request.params,
                                request_type)
        if result is None:
            raise webob.exc.HTTPNotFound()
        last_modified, output = result

        if request_type == 'json':
            content_type = 'application/json'
        else:
            content_type = 'text/plain'

        response = webob.Response(body=output,
                                  charset='UTF-8',
                                  content_type=content_type)
        response.headers['Access-Control-Allow-Origin'] = '*'

        response.cache_control.public = True
        response.cache_control.max_age = self.cache_expiry
        response.last_modified = last_modified
        response.expires = last_modified + self.cache_expiry

        return response.conditional_response_app
