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

from nodepool import tests
from nodepool import zk
from nodepool.nodepool import NodeLaunchManager


class TestNodeLaunchManager(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestNodeLaunchManager")

    def test_successful_launch(self):
        n1 = zk.Node()
        n1.state = zk.BUILDING
        mgr = NodeLaunchManager(self.zk, 0)
        mgr.launch(n1)
        while not mgr.poll():
            time.sleep(0)
        self.assertEqual(len(mgr.ready_nodes), 1)
        self.assertEqual(len(mgr.failed_nodes), 0)

    @mock.patch('nodepool.nodepool.NodeLauncher._launchNode')
    def test_failed_launch(self, mock_launch):
        mock_launch.side_effect = Exception()
        n1 = zk.Node()
        n1.state = zk.BUILDING
        mgr = NodeLaunchManager(self.zk, 0)
        mgr.launch(n1)
        while not mgr.poll():
            time.sleep(0)
        self.assertEqual(len(mgr.failed_nodes), 1)
        self.assertEqual(len(mgr.ready_nodes), 0)

    @mock.patch('nodepool.nodepool.NodeLauncher._launchNode')
    def test_mixed_launch(self, mock_launch):
        mock_launch.side_effect = [None, Exception()]
        n1 = zk.Node()
        n1.state = zk.BUILDING
        n2 = zk.Node()
        n2.state = zk.BUILDING
        mgr = NodeLaunchManager(self.zk, 0)
        mgr.launch(n1)
        mgr.launch(n2)
        while not mgr.poll():
            time.sleep(0)
        self.assertEqual(len(mgr.failed_nodes), 1)
        self.assertEqual(len(mgr.ready_nodes), 1)
