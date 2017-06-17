# Copyright (C) 2017 Red Hat
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

import logging
import os

from nodepool import config as nodepool_config
from nodepool import tests
from nodepool import zk
from nodepool.cmd.config_validator import ConfigValidator
from voluptuous import MultipleInvalid


class TestDriverStatic(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverStatic")

    def test_static_validator(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'static_error.yaml')
        validator = ConfigValidator(config)
        self.assertRaises(MultipleInvalid, validator.validate)

    def test_static_config(self):
        configfile = self.setup_config('static.yaml')
        config = nodepool_config.loadConfig(configfile)
        self.assertIn('static-provider', config.providers)

    def test_static_handler(self):
        configfile = self.setup_config('static.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.log.debug("Waiting for min-ready nodes")
        node = self.waitForNodes('fake-label')
        self.assertEqual(len(node), 1)
        nodes = self.waitForNodes('fake-concurrent-label', 2)
        self.assertEqual(len(nodes), 2)

        node = node[0]
        self.log.debug("Marking first node as used %s", node.id)
        node.state = zk.USED
        self.zk.storeNode(node)
        self.waitForNodeDeletion(node)

        self.log.debug("Waiting for node to be re-available")
        node = self.waitForNodes('fake-label')
        self.assertEqual(len(node), 1)

    def test_static_multinode_handler(self):
        configfile = self.setup_config('static.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        req.node_types.append('fake-concurrent-label')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(len(req.nodes), 2)

    def test_static_multiprovider_handler(self):
        configfile = self.setup_config('multiproviders.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.wait_for_config(pool)
        manager = pool.getProviderManager('openstack-provider')
        manager._client.create_image(name="fake-image")

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-static-label')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(len(req.nodes), 1)

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-openstack-label')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(len(req.nodes), 1)
