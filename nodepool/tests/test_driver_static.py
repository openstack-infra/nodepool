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

    def test_static_basic(self):
        '''
        Test that basic node registration works.
        '''
        configfile = self.setup_config('static-basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.log.debug("Waiting for node pre-registration")
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        self.assertEqual(nodes[0].state, zk.READY)
        self.assertEqual(nodes[0].provider, "static-provider")
        self.assertEqual(nodes[0].pool, "main")
        self.assertEqual(nodes[0].launcher, "static driver")
        self.assertEqual(nodes[0].type, ['fake-label'])
        self.assertEqual(nodes[0].hostname, 'fake-host-1')
        self.assertEqual(nodes[0].interface_ip, 'fake-host-1')
        self.assertEqual(nodes[0].username, 'zuul')
        self.assertEqual(nodes[0].connection_port, 22022)
        self.assertEqual(nodes[0].connection_type, 'ssh')
        self.assertEqual(nodes[0].host_keys, ['ssh-rsa FAKEKEY'])

    def test_static_node_increase(self):
        '''
        Test that adding new nodes to the config creates additional nodes.
        '''
        configfile = self.setup_config('static-basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.log.debug("Waiting for initial node")
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        self.log.debug("Waiting for additional node")
        self.replace_config(configfile, 'static-2-nodes.yaml')
        nodes = self.waitForNodes('fake-label', 2)
        self.assertEqual(len(nodes), 2)

    def test_static_node_decrease(self):
        '''
        Test that removing nodes from the config removes nodes.
        '''
        configfile = self.setup_config('static-2-nodes.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.log.debug("Waiting for initial nodes")
        nodes = self.waitForNodes('fake-label', 2)
        self.assertEqual(len(nodes), 2)

        self.log.debug("Waiting for node decrease")
        self.replace_config(configfile, 'static-basic.yaml')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].hostname, 'fake-host-1')

    def test_static_parallel_increase(self):
        '''
        Test that increasing max-parallel-jobs creates additional nodes.
        '''
        configfile = self.setup_config('static-basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.log.debug("Waiting for initial node")
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        self.log.debug("Waiting for additional node")
        self.replace_config(configfile, 'static-parallel-increase.yaml')
        nodes = self.waitForNodes('fake-label', 2)
        self.assertEqual(len(nodes), 2)

    def test_static_parallel_decrease(self):
        '''
        Test that decreasing max-parallel-jobs deletes nodes.
        '''
        configfile = self.setup_config('static-parallel-increase.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.log.debug("Waiting for initial nodes")
        nodes = self.waitForNodes('fake-label', 2)
        self.assertEqual(len(nodes), 2)

        self.log.debug("Waiting for node decrease")
        self.replace_config(configfile, 'static-basic.yaml')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

    def test_static_multilabel(self):
        configfile = self.setup_config('static-multilabel.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label')
        self.assertIn('fake-label', nodes[0].type)
        self.assertIn('fake-label2', nodes[0].type)

    def test_static_handler(self):
        configfile = self.setup_config('static.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        node = self.waitForNodes('fake-label')
        self.waitForNodes('fake-concurrent-label', 2)

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

    def test_static_request_handled(self):
        '''
        Test that a node is reregistered after handling a request.
        '''
        configfile = self.setup_config('static-basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(len(req.nodes), 1)
        self.assertEqual(req.nodes[0], nodes[0].id)

        # Mark node as used
        nodes[0].state = zk.USED
        self.zk.storeNode(nodes[0])

        # Our single node should have been used, deleted, then reregistered
        new_nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(new_nodes), 1)
        self.assertEqual(nodes[0].hostname, new_nodes[0].hostname)
