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

import logging
import time
import fixtures

from nodepool import tests
from nodepool import zk
import nodepool.fakeprovider
import nodepool.nodepool


class TestNodepool(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestNodepool")

    def test_node_assignment(self):
        '''
        Successful node launch should have unlocked nodes in READY state
        and assigned to the request.
        '''
        configfile = self.setup_config('node.yaml')
        self._useBuilder(configfile)
        image = self.waitForImage('fake-provider', 'fake-image')

        nodepool.nodepool.LOCK_CLEANUP = 1
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        for node_id in req.nodes:
            node = self.zk.getNode(node_id)
            self.assertEqual(node.allocated_to, req.id)
            self.assertEqual(node.state, zk.READY)
            self.assertIsNotNone(node.launcher)
            self.assertEqual(node.az, "az1")
            p = "{path}/{id}".format(
                path=self.zk._imageUploadPath(image.image_name,
                                              image.build_id,
                                              image.provider_name),
                id=image.id)
            self.assertEqual(node.image_id, p)
            self.zk.lockNode(node, blocking=False)
            self.zk.unlockNode(node)

        # Verify the cleanup thread removed the lock
        self.assertIsNotNone(
            self.zk.client.exists(self.zk._requestLockPath(req.id))
        )
        self.zk.deleteNodeRequest(req)
        self.waitForNodeRequestLockDeletion(req.id)
        self.assertReportedStat('nodepool.nodes.ready', '1|g')
        self.assertReportedStat('nodepool.nodes.building', '0|g')

    def test_node_assignment_at_quota(self):
        '''
        Successful node launch should have unlocked nodes in READY state
        and assigned to the request.
        '''
        configfile = self.setup_config('node_quota.yaml')
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')

        nodepool.nodepool.LOCK_CLEANUP = 1
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)

        client = pool.getProviderManager('fake-provider')._getClient()

        # One of the things we want to test is that if we spawn many
        # node launches at once, we do not deadlock while the request
        # handler pauses for quota.  To ensure we test that case,
        # pause server creation until we have accepted all of the node
        # requests we submit.  This will ensure that we hold locks on
        # all of the nodes before pausing so that we can validate they
        # are released.
        client.pause_creates = True

        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.node_types.append('fake-label')
        req1.node_types.append('fake-label')
        self.zk.storeNodeRequest(req1)
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('fake-label')
        req2.node_types.append('fake-label')
        self.zk.storeNodeRequest(req2)

        req1 = self.waitForNodeRequest(req1, (zk.PENDING,))
        req2 = self.waitForNodeRequest(req2, (zk.PENDING,))

        # At this point, we should be about to create or have already
        # created two servers for the first request, and the request
        # handler has accepted the second node request but paused
        # waiting for the server count to go below quota.

        # Wait until both of the servers exist.
        while len(client._server_list) < 2:
            time.sleep(0.1)

        # Allow the servers to finish being created.
        for server in client._server_list:
            server.event.set()

        self.log.debug("Waiting for 1st request %s", req1.id)
        req1 = self.waitForNodeRequest(req1)
        self.assertEqual(req1.state, zk.FULFILLED)
        self.assertEqual(len(req1.nodes), 2)

        # Mark the first request's nodes as USED, which will get them deleted
        # and allow the second to proceed.
        self.log.debug("Marking first node as used %s", req1.id)
        node = self.zk.getNode(req1.nodes[0])
        node.state = zk.USED
        self.zk.storeNode(node)
        self.waitForNodeDeletion(node)

        # To force the sequential nature of what we're testing, wait for
        # the 2nd request to get a node allocated to it now that we've
        # freed up a node.
        self.log.debug("Waiting for node allocation for 2nd request")
        done = False
        while not done:
            for n in self.zk.nodeIterator():
                if n.allocated_to == req2.id:
                    done = True
                    break

        self.log.debug("Marking second node as used %s", req1.id)
        node = self.zk.getNode(req1.nodes[1])
        node.state = zk.USED
        self.zk.storeNode(node)
        self.waitForNodeDeletion(node)

        self.log.debug("Deleting 1st request %s", req1.id)
        self.zk.deleteNodeRequest(req1)
        self.waitForNodeRequestLockDeletion(req1.id)

        # Wait until both of the servers exist.
        while len(client._server_list) < 2:
            time.sleep(0.1)

        # Allow the servers to finish being created.
        for server in client._server_list:
            server.event.set()

        req2 = self.waitForNodeRequest(req2)
        self.assertEqual(req2.state, zk.FULFILLED)
        self.assertEqual(len(req2.nodes), 2)

    def test_fail_request_on_launch_failure(self):
        '''
        Test that provider launch error fails the request.
        '''
        configfile = self.setup_config('node_launch_retry.yaml')
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager.createServer_fails = 2

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(0, manager.createServer_fails)
        self.assertEqual(req.state, zk.FAILED)
        self.assertNotEqual(req.declined_by, [])

    def test_invalid_image_fails(self):
        '''
        Test that an invalid image declines and fails the request.
        '''
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append("zorky-zumba")
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FAILED)
        self.assertNotEqual(req.declined_by, [])

    def test_node(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertNotEqual(nodes[0].host_keys, [])

    def test_disabled_label(self):
        """Test that a node is not created with min-ready=0"""
        configfile = self.setup_config('node_disabled_label.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual([], self.zk.getNodeRequests())
        self.assertEqual([], self.zk.getNodes())

    def test_node_net_name(self):
        """Test that a node is created with a net name"""
        configfile = self.setup_config('node_net_name.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')

    def test_node_vhd_image(self):
        """Test that a image and node are created vhd image"""
        configfile = self.setup_config('node_vhd.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')

    def test_node_vhd_and_qcow2(self):
        """Test label provided by vhd and qcow2 images builds"""
        configfile = self.setup_config('node_vhd_and_qcow2.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        self.waitForImage('fake-provider1', 'fake-image')
        self.waitForImage('fake-provider2', 'fake-image')
        pool.start()
        nodes = self.waitForNodes('fake-label', 2)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(zk.READY, nodes[0].state)
        self.assertEqual(zk.READY, nodes[1].state)
        if nodes[0].provider == 'fake-provider1':
            self.assertEqual(nodes[1].provider, 'fake-provider2')
        else:
            self.assertEqual(nodes[1].provider, 'fake-provider1')

    def test_dib_upload_fail(self):
        """Test that an image upload failure is contained."""
        configfile = self.setup_config('node_upload_fail.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider2', 'fake-image')
        nodes = self.waitForNodes('fake-label', 2)
        self.assertEqual(len(nodes), 2)
        total_nodes = sum(1 for _ in self.zk.nodeIterator())
        self.assertEqual(total_nodes, 2)
        self.assertEqual(nodes[0].provider, 'fake-provider2')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertEqual(nodes[1].provider, 'fake-provider2')
        self.assertEqual(nodes[1].type, 'fake-label')

    def test_node_az(self):
        """Test that an image and node are created with az specified"""
        configfile = self.setup_config('node_az.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].az, 'az1')

    def test_node_ipv6(self):
        """Test that ipv6 existence either way works fine."""
        configfile = self.setup_config('node_ipv6.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider1', 'fake-image')
        self.waitForImage('fake-provider2', 'fake-image')
        label1_nodes = self.waitForNodes('fake-label1')
        label2_nodes = self.waitForNodes('fake-label2')

        self.assertEqual(len(label1_nodes), 1)
        self.assertEqual(len(label2_nodes), 1)

        # ipv6 address available
        self.assertEqual(label1_nodes[0].provider, 'fake-provider1')
        self.assertEqual(label1_nodes[0].public_ipv4, 'fake')
        self.assertEqual(label1_nodes[0].public_ipv6, 'fake_v6')
        self.assertEqual(label1_nodes[0].interface_ip, 'fake_v6')

        # ipv6 address unavailable
        self.assertEqual(label2_nodes[0].provider, 'fake-provider2')
        self.assertEqual(label2_nodes[0].public_ipv4, 'fake')
        self.assertEqual(label2_nodes[0].public_ipv6, '')
        self.assertEqual(label2_nodes[0].interface_ip, 'fake')

    def test_node_delete_success(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(zk.READY, nodes[0].state)
        self.assertEqual('fake-provider', nodes[0].provider)
        nodes[0].state = zk.DELETING
        self.zk.storeNode(nodes[0])

        # Wait for this one to be deleted
        self.waitForNodeDeletion(nodes[0])

        # Wait for a new one to take it's place
        new_nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(new_nodes), 1)
        self.assertEqual(zk.READY, new_nodes[0].state)
        self.assertEqual('fake-provider', new_nodes[0].provider)
        self.assertNotEqual(nodes[0], new_nodes[0])

    def test_node_launch_retries(self):
        configfile = self.setup_config('node_launch_retry.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager.createServer_fails = 2
        self.waitForImage('fake-provider', 'fake-image')

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FAILED)

        # retries in config is set to 2, so 2 attempts to create a server
        self.assertEqual(0, manager.createServer_fails)

    def test_node_delete_failure(self):
        def fail_delete(self, name):
            raise RuntimeError('Fake Error')

        fake_delete = 'nodepool.provider_manager.FakeProviderManager.deleteServer'
        self.useFixture(fixtures.MonkeyPatch(fake_delete, fail_delete))

        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        self.zk.lockNode(nodes[0], blocking=False)
        nodepool.nodepool.InstanceDeleter.delete(
            self.zk, pool.getProviderManager('fake-provider'), nodes[0])

        # Make sure our old node is in delete state, even though delete failed
        deleted_node = self.zk.getNode(nodes[0].id)
        self.assertIsNotNone(deleted_node)
        self.assertEqual(deleted_node.state, zk.DELETING)

        # Make sure we have a new, READY node
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')

    def test_leaked_node(self):
        """Test that a leaked node is deleted"""
        configfile = self.setup_config('leaked_node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for initial pool.")

        # Make sure we have a node built and ready
        self.assertEqual(len(nodes), 1)
        manager = pool.getProviderManager('fake-provider')
        servers = manager.listServers()
        self.assertEqual(len(servers), 1)

        # Delete the node from ZooKeeper, but leave the instance
        # so it is leaked.
        self.log.debug("Delete node db record so instance is leaked...")
        self.zk.deleteNode(nodes[0])
        self.log.debug("...deleted node db so instance is leaked.")

        # Wait for nodepool to replace it
        self.log.debug("Waiting for replacement pool...")
        new_nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for replacement pool.")
        self.assertEqual(len(new_nodes), 1)

        # Wait for the instance to be cleaned up
        self.waitForInstanceDeletion(manager, nodes[0].external_id)

        # Make sure we end up with only one server (the replacement)
        servers = manager.listServers()
        self.assertEqual(len(servers), 1)

    def test_label_provider(self):
        """Test that only providers listed in the label satisfy the request"""
        configfile = self.setup_config('node_label_provider.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForImage('fake-provider2', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider2')

    def _create_pending_request(self):
        req = zk.NodeRequest()
        req.state = zk.PENDING
        req.requestor = 'test_nodepool'
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        # Create a node that is allocated to the request, but not yet assigned
        # within the NodeRequest object
        node = zk.Node()
        node.state = zk.READY
        node.type = 'fake-label'
        node.public_ipv4 = 'fake'
        node.provider = 'fake-provider'
        node.pool = 'main'
        node.allocated_to = req.id
        self.zk.storeNode(node)

        return (req, node)

    def test_lost_requests(self):
        """Test a request left pending is reset and satisfied on restart"""
        (req, node) = self._create_pending_request()

        configfile = self.setup_config('node_lost_requests.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()

        req = self.waitForNodeRequest(req, (zk.FULFILLED,))
        # Since our config file has min-ready=0, we should be able to re-use
        # the previously assigned node, thus making sure that the cleanup
        # code reset the 'allocated_to' field.
        self.assertIn(node.id, req.nodes)

    def test_node_deallocation(self):
        """Test an allocated node with a missing request is deallocated"""
        node = zk.Node()
        node.state = zk.READY
        node.type = 'fake-label'
        node.public_ipv4 = 'fake'
        node.provider = 'fake-provider'
        node.allocated_to = "MISSING"
        self.zk.storeNode(node)

        configfile = self.setup_config('node_lost_requests.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()

        while True:
            node = self.zk.getNode(node.id)
            if not node.allocated_to:
                break

    def test_multiple_pools(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('multiple_pools.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        lab1 = self.waitForNodes('fake-label1')
        lab2 = self.waitForNodes('fake-label2')

        self.assertEqual(len(lab1), 1)
        self.assertEqual(lab1[0].provider, 'fake-provider')
        self.assertEqual(lab1[0].type, 'fake-label1')
        self.assertEqual(lab1[0].az, 'az1')
        self.assertEqual(lab1[0].pool, 'pool1')

        self.assertEqual(len(lab2), 1)
        self.assertEqual(lab2[0].provider, 'fake-provider')
        self.assertEqual(lab2[0].type, 'fake-label2')
        self.assertEqual(lab2[0].az, 'az2')
        self.assertEqual(lab2[0].pool, 'pool2')
