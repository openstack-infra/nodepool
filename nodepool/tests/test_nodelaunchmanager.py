# Copyright (C) 2017 Red Hat, Inc.
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
import mock
import time

from nodepool import builder
from nodepool import provider_manager
from nodepool import tests
from nodepool import zk
from nodepool.driver.openstack.handler import OpenStackNodeRequestHandler


class TestNodeLaunchManager(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestNodeLaunchManager")

    def _setup(self, configfile):
        # Need a builder for the launch code to work and to access
        # config objects.
        b = builder.NodePoolBuilder(configfile)
        b.cleanup_interval = .5
        b.build_interval = .1
        b.upload_interval = .1
        b.dib_cmd = 'nodepool/tests/fake-image-create'
        b.start()
        self.addCleanup(b.stop)
        self.waitForImage('fake-provider', 'fake-image')

        self.provider = b._config.providers['fake-provider']
        self.provider_pool = self.provider.pools['main']

        # The builder config does not have a provider manager, so create one.
        self.pmanager = provider_manager.get_provider(
            self.provider, False)
        self.pmanager.resetClient()

    def _createHandler(self, retries=1):
        # Mock NodeRequest handler object
        class FakePoolWorker:
            launcher_id = 'Test'

        class FakeRequest:
            requestor = 'zuul'

        class FakeProvider:
            launch_retries = retries

        handler = OpenStackNodeRequestHandler(FakePoolWorker(), FakeRequest())
        handler.zk = self.zk
        handler.pool = self.provider_pool
        handler.manager = self.pmanager
        handler.provider = FakeProvider()
        return handler

    def _launch(self, handler, node):
        # Mock NodeRequest runHandler method
        thread = handler.launch(node)
        thread.start()
        handler._threads.append(thread)
        handler.nodeset.append(node)

    def test_successful_launch(self):
        configfile = self.setup_config('node.yaml')
        self._setup(configfile)
        handler = self._createHandler(1)

        n1 = zk.Node()
        n1.state = zk.BUILDING
        n1.type = 'fake-label'
        self._launch(handler, n1)
        while not handler.pollLauncher():
            time.sleep(0)
        self.assertEqual(len(handler.ready_nodes), 1)
        self.assertEqual(len(handler.failed_nodes), 0)
        nodes = handler.manager.listNodes()
        self.assertEqual(nodes[0]['metadata']['groups'],
                         'fake-provider,fake-image,fake-label')

    @mock.patch('nodepool.driver.openstack.handler.'
                'OpenStackNodeLauncher._launchNode')
    def test_failed_launch(self, mock_launch):
        configfile = self.setup_config('node.yaml')
        self._setup(configfile)
        handler = self._createHandler(1)

        mock_launch.side_effect = Exception()
        n1 = zk.Node()
        n1.state = zk.BUILDING
        n1.type = 'fake-label'
        self._launch(handler, n1)
        while not handler.pollLauncher():
            time.sleep(0)
        self.assertEqual(len(handler.failed_nodes), 1)
        self.assertEqual(len(handler.ready_nodes), 0)

    @mock.patch('nodepool.driver.openstack.handler.'
                'OpenStackNodeLauncher._launchNode')
    def test_mixed_launch(self, mock_launch):
        configfile = self.setup_config('node.yaml')
        self._setup(configfile)
        handler = self._createHandler(1)

        mock_launch.side_effect = [None, Exception()]
        n1 = zk.Node()
        n1.state = zk.BUILDING
        n1.type = 'fake-label'
        n2 = zk.Node()
        n2.state = zk.BUILDING
        n2.type = 'fake-label'
        self._launch(handler, n1)
        self._launch(handler, n2)
        while not handler.pollLauncher():
            time.sleep(0)
        self.assertEqual(len(handler._failed_nodes), 1)
        self.assertEqual(len(handler._ready_nodes), 1)
