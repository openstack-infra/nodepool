# Copyright (C) 2014 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import yaml
from urllib import request

from nodepool import tests
from nodepool import zk


class TestWebApp(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestWebApp")

    # A standard browser accept header
    browser_accept = (
        'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')

    def test_image_list(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        webapp = self.useWebApp(pool, port=0)
        webapp.start()
        port = webapp.server.socket.getsockname()[1]

        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        req = request.Request(
            "http://localhost:%s/image-list" % port)
        # NOTE(ianw): we want pretty printed text/plain back, but
        # simulating a normal web-browser request.
        req.add_header('Accept', self.browser_accept)
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'text/plain; charset=UTF-8')
        data = f.read()
        self.assertTrue('fake-image' in data.decode('utf8'))

        # also ensure that text/plain works (might be hand-set by a
        # command-line curl script, etc)
        req = request.Request(
            "http://localhost:%s/image-list" % port)
        req.add_header('Accept', 'text/plain')
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'text/plain; charset=UTF-8')
        data = f.read()
        self.assertTrue('fake-image' in data.decode('utf8'))

    def test_image_list_filtered(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        webapp = self.useWebApp(pool, port=0)
        webapp.start()
        port = webapp.server.socket.getsockname()[1]

        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        req = request.Request(
            "http://localhost:%s/image-list?fields=id,image,state" % port)
        req.add_header('Accept', self.browser_accept)
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'text/plain; charset=UTF-8')
        data = f.read()
        self.assertIn("| 0000000001 | fake-image | ready |",
                      data.decode('utf8'))

    def test_image_list_json(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        webapp = self.useWebApp(pool, port=0)
        webapp.start()
        port = webapp.server.socket.getsockname()[1]

        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        req = request.Request(
            "http://localhost:%s/image-list" % port)
        req.add_header('Accept', 'application/json')
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'application/json')
        data = f.read()
        objs = json.loads(data.decode('utf8'))
        self.assertDictContainsSubset({'id': '0000000001',
                                       'image': 'fake-image',
                                       'provider': 'fake-provider',
                                       'state': 'ready'}, objs[0])

    def test_dib_image_list_json(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        webapp = self.useWebApp(pool, port=0)
        webapp.start()
        port = webapp.server.socket.getsockname()[1]

        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        req = request.Request(
            "http://localhost:%s/dib-image-list" % port)
        req.add_header('Accept', 'application/json')
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'application/json')
        data = f.read()
        objs = json.loads(data.decode('utf8'))
        # make sure this is valid json and has some of the
        # non-changing keys
        self.assertDictContainsSubset({'id': 'fake-image-0000000001',
                                       'formats': ['qcow2'],
                                       'state': 'ready'}, objs[0])

    def test_node_list_json(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        webapp = self.useWebApp(pool, port=0)
        webapp.start()
        port = webapp.server.socket.getsockname()[1]

        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        req = request.Request(
            "http://localhost:%s/node-list" % port)
        req.add_header('Accept', 'application/json')
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'application/json')
        data = f.read()
        objs = json.loads(data.decode('utf8'))
        self.assertDictContainsSubset({'id': '0000000000',
                                       'ipv6': '',
                                       'label': ['fake-label'],
                                       'locked': 'unlocked',
                                       'provider': 'fake-provider',
                                       'public_ipv4': 'fake',
                                       'state': 'ready'}, objs[0])
        # specify valid node_id
        req = request.Request(
            "http://localhost:%s/node-list?node_id=%s" % (port,
                                                          '0000000000'))
        req.add_header('Accept', 'application/json')
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'application/json')
        data = f.read()
        objs = json.loads(data.decode('utf8'))
        self.assertDictContainsSubset({'id': '0000000000',
                                       'ipv6': '',
                                       'label': ['fake-label'],
                                       'locked': 'unlocked',
                                       'provider': 'fake-provider',
                                       'public_ipv4': 'fake',
                                       'state': 'ready'}, objs[0])
        # node_id not found
        req = request.Request(
            "http://localhost:%s/node-list?node_id=%s" % (port,
                                                          '999999'))
        req.add_header('Accept', 'application/json')
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'application/json')
        data = f.read()
        objs = json.loads(data.decode('utf8'))
        self.assertEqual(0, len(objs), objs)

    def test_request_list_json(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        webapp = self.useWebApp(pool, port=0)
        webapp.start()
        port = webapp.server.socket.getsockname()[1]

        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')
        req = zk.NodeRequest()
        req.state = zk.PENDING   # so it will be ignored
        req.node_types = ['fake-label']
        req.requestor = 'test_request_list'
        self.zk.storeNodeRequest(req)

        http_req = request.Request(
            "http://localhost:%s/request-list" % port)
        http_req.add_header('Accept', 'application/json')
        f = request.urlopen(http_req)
        self.assertEqual(f.info().get('Content-Type'),
                         'application/json')
        data = f.read()
        objs = json.loads(data.decode('utf8'))
        self.assertDictContainsSubset({'node_types': ['fake-label'],
                                       'requestor': 'test_request_list', },
                                      objs[0])

    def test_label_list_json(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        webapp = self.useWebApp(pool, port=0)
        webapp.start()
        port = webapp.server.socket.getsockname()[1]

        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        req = request.Request(
            "http://localhost:%s/label-list" % port)
        req.add_header('Accept', 'application/json')
        f = request.urlopen(req)
        self.assertEqual(f.info().get('Content-Type'),
                         'application/json')
        data = f.read()
        objs = json.loads(data.decode('utf8'))
        self.assertEqual([{'label': 'fake-label'}], objs)

    def test_webapp_config(self):
        configfile = self.setup_config('webapp.yaml')
        config = yaml.safe_load(open(configfile))
        self.assertEqual(config['webapp']['port'], 8080)
        self.assertEqual(config['webapp']['listen_address'], '127.0.0.1')
