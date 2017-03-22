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
from nodepool.nodepool import NodeLaunchManager


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
        self.pmanager = provider_manager.ProviderManager(self.provider, False)
        self.pmanager.resetClient()

    def test_successful_launch(self):
        configfile = self.setup_config('node.yaml')
        self._setup(configfile)

        n1 = zk.Node()
        n1.state = zk.BUILDING
        n1.type = 'fake-label'
        mgr = NodeLaunchManager(self.zk, self.provider_pool,
                                self.pmanager, 'zuul', 1)
        mgr.launch(n1)
        while not mgr.poll():
            time.sleep(0)
        self.assertEqual(len(mgr.ready_nodes), 1)
        self.assertEqual(len(mgr.failed_nodes), 0)

    @mock.patch('nodepool.nodepool.NodeLauncher._launchNode')
    def test_failed_launch(self, mock_launch):
        configfile = self.setup_config('node.yaml')
        self._setup(configfile)

        mock_launch.side_effect = Exception()
        n1 = zk.Node()
        n1.state = zk.BUILDING
        n1.type = 'fake-label'
        mgr = NodeLaunchManager(self.zk, self.provider_pool,
                                self.pmanager, 'zuul', 1)
        mgr.launch(n1)
        while not mgr.poll():
            time.sleep(0)
        self.assertEqual(len(mgr.failed_nodes), 1)
        self.assertEqual(len(mgr.ready_nodes), 0)

    @mock.patch('nodepool.nodepool.NodeLauncher._launchNode')
    def test_mixed_launch(self, mock_launch):
        configfile = self.setup_config('node.yaml')
        self._setup(configfile)

        mock_launch.side_effect = [None, Exception()]
        n1 = zk.Node()
        n1.state = zk.BUILDING
        n1.type = 'fake-label'
        n2 = zk.Node()
        n2.state = zk.BUILDING
        n2.type = 'fake-label'
        mgr = NodeLaunchManager(self.zk, self.provider_pool,
                                self.pmanager, 'zuul', 1)
        mgr.launch(n1)
        mgr.launch(n2)
        while not mgr.poll():
            time.sleep(0)
        self.assertEqual(len(mgr.failed_nodes), 1)
        self.assertEqual(len(mgr.ready_nodes), 1)
