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
import math
import time
import fixtures

from nodepool import tests
from nodepool import zk
from nodepool.driver import Drivers
import nodepool.launcher


class TestLauncher(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestLauncher")

    def test_node_assignment(self):
        '''
        Successful node launch should have unlocked nodes in READY state
        and assigned to the request.
        '''
        configfile = self.setup_config('node_no_min_ready.yaml')
        self.useBuilder(configfile)
        image = self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual(image.username, 'zuul')

        nodepool.launcher.LOCK_CLEANUP = 1
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
            self.assertEqual(node.cloud, 'fake')
            self.assertEqual(node.region, 'fake-region')
            self.assertEqual(node.az, "az1")
            self.assertEqual(node.username, "zuul")
            self.assertEqual(node.connection_type, 'ssh')
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

    def test_node_assignment_order(self):
        """Test that nodes are assigned in the order requested"""
        configfile = self.setup_config('node_many_labels.yaml')
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.waitForNodes('fake-label1')
        self.waitForNodes('fake-label2')
        self.waitForNodes('fake-label3')
        self.waitForNodes('fake-label4')

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label3')
        req.node_types.append('fake-label1')
        req.node_types.append('fake-label4')
        req.node_types.append('fake-label2')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(4, len(req.nodes))
        nodes = []
        for node_id in req.nodes:
            nodes.append(self.zk.getNode(node_id))
        self.assertEqual(nodes[0].type, 'fake-label3')
        self.assertEqual(nodes[1].type, 'fake-label1')
        self.assertEqual(nodes[2].type, 'fake-label4')
        self.assertEqual(nodes[3].type, 'fake-label2')

    def _test_node_assignment_at_quota(self,
                                       config,
                                       max_cores=100,
                                       max_instances=20,
                                       max_ram=1000000):
        '''
        Successful node launch should have unlocked nodes in READY state
        and assigned to the request. This should be run with a quota that
        fits for two nodes.
        '''

        # patch the cloud with requested quota
        def fake_get_quota():
            return (max_cores, max_instances, max_ram)
        self.useFixture(fixtures.MockPatchObject(
            Drivers.get('fake')['provider'].fake_cloud, '_get_quota',
            fake_get_quota
        ))

        configfile = self.setup_config(config)
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')

        nodepool.launcher.LOCK_CLEANUP = 1
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)

        client = pool.getProviderManager('fake-provider')._getClient()

        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.node_types.append('fake-label')
        req1.node_types.append('fake-label')
        self.zk.storeNodeRequest(req1)

        self.log.debug("Waiting for 1st request %s", req1.id)
        req1 = self.waitForNodeRequest(req1, (zk.FULFILLED,))
        self.assertEqual(len(req1.nodes), 2)

        # Mark the first request's nodes as in use so they won't be deleted
        # when we pause. Locking them is enough.
        req1_node1 = self.zk.getNode(req1.nodes[0])
        req1_node2 = self.zk.getNode(req1.nodes[1])
        self.zk.lockNode(req1_node1, blocking=False)
        self.zk.lockNode(req1_node2, blocking=False)

        # One of the things we want to test is that if we spawn many
        # node launches at once, we do not deadlock while the request
        # handler pauses for quota.  To ensure we test that case,
        # pause server creation until we have accepted all of the node
        # requests we submit.  This will ensure that we hold locks on
        # all of the nodes before pausing so that we can validate they
        # are released.
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('fake-label')
        req2.node_types.append('fake-label')
        self.zk.storeNodeRequest(req2)
        req2 = self.waitForNodeRequest(req2, (zk.PENDING,))

        # At this point, we should have already created two servers for the
        # first request, and the request handler has accepted the second node
        # request but paused waiting for the server count to go below quota.
        # Wait until there is a paused request handler and check if there
        # are exactly two servers
        pool_worker = pool.getPoolWorkers('fake-provider')
        while not pool_worker[0].paused_handler:
            time.sleep(0.1)
        self.assertEqual(len(client._server_list), 2)

        # Mark the first request's nodes as USED, which will get them deleted
        # and allow the second to proceed.
        self.log.debug("Marking first node as used %s", req1.id)
        req1_node1.state = zk.USED
        self.zk.storeNode(req1_node1)
        self.zk.unlockNode(req1_node1)
        self.waitForNodeDeletion(req1_node1)

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
        req1_node2.state = zk.USED
        self.zk.storeNode(req1_node2)
        self.zk.unlockNode(req1_node2)
        self.waitForNodeDeletion(req1_node2)

        self.log.debug("Deleting 1st request %s", req1.id)
        self.zk.deleteNodeRequest(req1)
        self.waitForNodeRequestLockDeletion(req1.id)

        req2 = self.waitForNodeRequest(req2, (zk.FULFILLED,))
        self.assertEqual(len(req2.nodes), 2)

    def test_node_assignment_at_pool_quota_cores(self):
        self._test_node_assignment_at_quota(
            config='node_quota_pool_cores.yaml')

    def test_node_assignment_at_pool_quota_instances(self):
        self._test_node_assignment_at_quota(
            config='node_quota_pool_instances.yaml')

    def test_node_assignment_at_pool_quota_ram(self):
        self._test_node_assignment_at_quota(
            config='node_quota_pool_ram.yaml')

    def test_node_assignment_at_cloud_cores_quota(self):
        self._test_node_assignment_at_quota(config='node_quota_cloud.yaml',
                                            max_cores=8,
                                            # check that -1 and inf work for no
                                            # quota
                                            max_instances=-1,
                                            max_ram=math.inf)

    def test_node_assignment_at_cloud_instances_quota(self):
        self._test_node_assignment_at_quota(config='node_quota_cloud.yaml',
                                            max_cores=math.inf,
                                            max_instances=2,
                                            max_ram=math.inf)

    def test_node_assignment_at_cloud_ram_quota(self):
        self._test_node_assignment_at_quota(config='node_quota_cloud.yaml',
                                            max_cores=math.inf,
                                            max_instances=math.inf,
                                            max_ram=2 * 8192)

    def test_over_quota(self, config='node_quota_cloud.yaml'):
        '''
        This tests what happens when a cloud unexpectedly returns an
        over-quota error.

        '''
        # Start with an instance quota of 2
        max_cores = math.inf
        max_instances = 2
        max_ram = math.inf

        # patch the cloud with requested quota
        def fake_get_quota():
            return (max_cores, max_instances, max_ram)
        self.useFixture(fixtures.MockPatchObject(
            Drivers.get('fake')['provider'].fake_cloud, '_get_quota',
            fake_get_quota
        ))

        configfile = self.setup_config(config)
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')

        nodepool.launcher.LOCK_CLEANUP = 1
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)

        client = pool.getProviderManager('fake-provider')._getClient()

        # Wait for a single node to be created
        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.node_types.append('fake-label')
        self.log.debug("Adding first request")
        self.zk.storeNodeRequest(req1)
        req1 = self.waitForNodeRequest(req1)
        self.assertEqual(req1.state, zk.FULFILLED)

        # Lock this node so it appears as used and not deleted
        req1_node = self.zk.getNode(req1.nodes[0])
        self.zk.lockNode(req1_node, blocking=False)

        # Now, reduce the quota so the next node unexpectedly
        # (according to nodepool's quota estimate) fails.
        client.max_instances = 1

        # Request a second node; this request should fail.
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('fake-label')
        self.log.debug("Adding second request")
        self.zk.storeNodeRequest(req2)
        req2 = self.waitForNodeRequest(req2)
        self.assertEqual(req2.state, zk.FAILED)

        # After the second request failed, the internal quota estimate
        # should be reset, so the next request should pause to wait
        # for more quota to become available.
        req3 = zk.NodeRequest()
        req3.state = zk.REQUESTED
        req3.node_types.append('fake-label')
        self.log.debug("Adding third request")
        self.zk.storeNodeRequest(req3)
        req3 = self.waitForNodeRequest(req3, (zk.PENDING,))
        self.assertEqual(req3.state, zk.PENDING)

        # Wait until there is a paused request handler and verify that
        # there is still only one server built (from the first
        # request).
        pool_worker = pool.getPoolWorkers('fake-provider')
        while not pool_worker[0].paused_handler:
            time.sleep(0.1)
        self.assertEqual(len(client._server_list), 1)

    def test_fail_request_on_launch_failure(self):
        '''
        Test that provider launch error fails the request.
        '''
        configfile = self.setup_config('node_launch_retry.yaml')
        self.useBuilder(configfile)
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

    def test_fail_minready_request_at_capacity(self):
        '''
        A min-ready request to a provider that is already at capacity should
        be declined.
        '''
        configfile = self.setup_config('node_min_ready_capacity.yaml')
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Get an initial node ready
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append("fake-label")
        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        # Now simulate a min-ready request
        min_ready_req = zk.NodeRequest()
        min_ready_req.state = zk.REQUESTED
        min_ready_req.node_types.append("fake-label")
        min_ready_req.requestor = "NodePool:min-ready"
        self.zk.storeNodeRequest(min_ready_req)
        min_ready_req = self.waitForNodeRequest(min_ready_req)
        self.assertEqual(min_ready_req.state, zk.FAILED)
        self.assertNotEqual(min_ready_req.declined_by, [])

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
        self.useBuilder(configfile)
        pool.start()
        image = self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual(image.username, 'zuul')
        nodes = self.waitForNodes('fake-label')

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertEqual(nodes[0].username, 'zuul')
        self.assertNotEqual(nodes[0].host_keys, [])

    def test_node_boot_from_volume(self):
        """Test that an image and node are created from a volume"""
        configfile = self.setup_config('node_boot_from_volume.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')

    def test_disabled_label(self):
        """Test that a node is not created with min-ready=0"""
        configfile = self.setup_config('node_disabled_label.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual([], self.zk.getNodeRequests())
        self.assertEqual([], self.zk.getNodes())

    def test_node_net_name(self):
        """Test that a node is created with a net name"""
        configfile = self.setup_config('node_net_name.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertEqual(nodes[0].username, 'zuul')

    def test_node_flavor_name(self):
        """Test that a node is created with a flavor name"""
        configfile = self.setup_config('node_flavor_name.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
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
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')

    def test_node_vhd_and_qcow2(self):
        """Test label provided by vhd and qcow2 images builds"""
        configfile = self.setup_config('node_vhd_and_qcow2.yaml')
        self.useBuilder(configfile)
        p1_image = self.waitForImage('fake-provider1', 'fake-image')
        p2_image = self.waitForImage('fake-provider2', 'fake-image')

        # We can't guarantee which provider would build the requested
        # nodes, but that doesn't matter so much as guaranteeing that the
        # correct image type is uploaded to the correct provider.
        self.assertEqual(p1_image.format, "vhd")
        self.assertEqual(p2_image.format, "qcow2")

    def test_dib_upload_fail(self):
        """Test that an image upload failure is contained."""
        configfile = self.setup_config('node_upload_fail.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider2', 'fake-image')
        nodes = self.waitForNodes('fake-label', 2)
        self.assertEqual(len(nodes), 2)
        total_nodes = sum(1 for _ in self.zk.nodeIterator())
        self.assertEqual(total_nodes, 2)
        self.assertEqual(nodes[0].provider, 'fake-provider2')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertEqual(nodes[0].username, 'zuul')
        self.assertEqual(nodes[1].provider, 'fake-provider2')
        self.assertEqual(nodes[1].type, 'fake-label')
        self.assertEqual(nodes[1].username, 'zuul')

    def test_node_az(self):
        """Test that an image and node are created with az specified"""
        configfile = self.setup_config('node_az.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
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
        self.useBuilder(configfile)
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
        self.useBuilder(configfile)
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
        self.useBuilder(configfile)
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

        self.useFixture(fixtures.MockPatchObject(
            Drivers.get('fake')['provider'], 'deleteServer', fail_delete))

        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        self.zk.lockNode(nodes[0], blocking=False)
        nodepool.launcher.NodeDeleter.delete(
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
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for initial pool.")

        # Make sure we have a node built and ready
        self.assertEqual(len(nodes), 1)
        manager = pool.getProviderManager('fake-provider')
        servers = manager.listNodes()
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
        servers = manager.listNodes()
        self.assertEqual(len(servers), 1)

    def test_max_ready_age(self):
        """Test a node with exceeded max-ready-age is deleted"""
        configfile = self.setup_config('node_max_ready_age.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for initial pool.")

        # Wait for the instance to be cleaned up
        manager = pool.getProviderManager('fake-provider')
        self.waitForInstanceDeletion(manager, nodes[0].external_id)

    def test_max_hold_age(self):
        """Test a held node with exceeded max-hold-age is deleted"""
        configfile = self.setup_config('node_max_hold_age.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for initial pool.")
        node = nodes[0]
        self.log.debug("Holding node %s..." % node.id)
        # hold the node
        node.state = zk.HOLD
        node.comment = 'testing'
        self.zk.lockNode(node, blocking=False)
        self.zk.storeNode(node)
        self.zk.unlockNode(node)
        znode = self.zk.getNode(node.id)
        self.log.debug("Node %s in state '%s'" % (znode.id, znode.state))
        # Wait for the instance to be cleaned up
        manager = pool.getProviderManager('fake-provider')
        self.waitForInstanceDeletion(manager, node.external_id)

    def test_hold_expiration_no_default(self):
        """Test a held node is deleted when past its operator-specified TTL,
        no max-hold-age set"""
        configfile = self.setup_config('node_max_ready_age.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for initial pool.")
        node = nodes[0]
        self.log.debug("Holding node %s..." % node.id)
        # hold the node
        node.state = zk.HOLD
        node.comment = 'testing'
        node.hold_expiration = 5
        self.zk.lockNode(node, blocking=False)
        self.zk.storeNode(node)
        self.zk.unlockNode(node)
        znode = self.zk.getNode(node.id)
        self.log.debug("Node %s in state '%s'" % (znode.id, znode.state))
        # Wait for the instance to be cleaned up
        manager = pool.getProviderManager('fake-provider')
        self.waitForInstanceDeletion(manager, node.external_id)

    def test_hold_expiration_lower_than_default(self):
        """Test a held node is deleted when past its operator-specified TTL,
        with max-hold-age set in the configuration"""
        configfile = self.setup_config('node_max_hold_age_2.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label', 2)
        self.log.debug("...done waiting for initial pool.")
        node_custom = nodes[0]
        # TODO make it a fraction of fixture's max-hold-age
        hold_expiration = 2
        node = nodes[1]
        self.log.debug("Holding node %s... (default)" % node.id)
        self.log.debug("Holding node %s...(%s seconds)" % (node_custom.id,
                                                           hold_expiration))
        # hold the nodes
        node.state = zk.HOLD
        node.comment = 'testing'
        node_custom.state = zk.HOLD
        node_custom.comment = 'testing hold_expiration'
        node_custom.hold_expiration = hold_expiration
        self.zk.lockNode(node, blocking=False)
        self.zk.storeNode(node)
        self.zk.unlockNode(node)
        self.zk.lockNode(node_custom, blocking=False)
        self.zk.storeNode(node_custom)
        self.zk.unlockNode(node_custom)
        znode = self.zk.getNode(node.id)
        self.log.debug("Node %s in state '%s'" % (znode.id, znode.state))
        znode_custom = self.zk.getNode(node_custom.id)
        self.log.debug("Node %s in state '%s'" % (znode_custom.id,
                                                  znode_custom.state))
        # Wait for the instance to be cleaned up
        manager = pool.getProviderManager('fake-provider')
        self.waitForInstanceDeletion(manager, node_custom.external_id)
        # control node should still be held
        held_nodes = [n for n in self.zk.nodeIterator() if n.state == zk.HOLD]
        self.assertTrue(any(n.id == node.id for n in held_nodes),
                        held_nodes)
        # finally, control node gets deleted
        self.waitForInstanceDeletion(manager, node.external_id)

    def test_hold_expiration_higher_than_default(self):
        """Test a held node is deleted after max-hold-age seconds if the
        operator specifies a larger TTL"""
        configfile = self.setup_config('node_max_hold_age_2.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label', 2)
        self.log.debug("...done waiting for initial pool.")
        node_custom = nodes[0]
        # TODO make it a multiple of fixture's max-hold-age
        hold_expiration = 20
        node = nodes[1]
        self.log.debug("Holding node %s... (default)" % node.id)
        self.log.debug("Holding node %s...(%s seconds)" % (node_custom.id,
                                                           hold_expiration))
        # hold the nodes
        node.state = zk.HOLD
        node.comment = 'testing'
        node_custom.state = zk.HOLD
        node_custom.comment = 'testing hold_expiration'
        node_custom.hold_expiration = hold_expiration
        self.zk.lockNode(node, blocking=False)
        self.zk.storeNode(node)
        self.zk.unlockNode(node)
        self.zk.lockNode(node_custom, blocking=False)
        self.zk.storeNode(node_custom)
        self.zk.unlockNode(node_custom)
        znode = self.zk.getNode(node.id)
        self.log.debug("Node %s in state '%s'" % (znode.id, znode.state))
        znode_custom = self.zk.getNode(node_custom.id)
        self.log.debug("Node %s in state '%s'" % (znode_custom.id,
                                                  znode_custom.state))
        # Wait for the instance to be cleaned up
        manager = pool.getProviderManager('fake-provider')
        self.waitForInstanceDeletion(manager, node.external_id)
        # custom node should be deleted as well
        held_nodes = [n for n in self.zk.nodeIterator() if n.state == zk.HOLD]
        self.assertEqual(0, len(held_nodes), held_nodes)

    def test_label_provider(self):
        """Test that only providers listed in the label satisfy the request"""
        configfile = self.setup_config('node_label_provider.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
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
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
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
        self.useBuilder(configfile)
        pool.start()

        while True:
            node = self.zk.getNode(node.id)
            if not node.allocated_to:
                break

    def test_multiple_pools(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('multiple_pools.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
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

    def test_unmanaged_image(self):
        """Test node launching using an unmanaged image"""
        configfile = self.setup_config('node_unmanaged_image.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)

        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager._client.create_image(name="fake-image")
        manager._client.create_image(name="fake-image-windows")

        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertIsNone(nodes[0].username)

        nodes = self.waitForNodes('fake-label-windows')
        self.assertEqual(len(nodes), 1)
        self.assertEqual('zuul', nodes[0].username)
        self.assertEqual('winrm', nodes[0].connection_type)

    def test_unmanaged_image_provider_name(self):
        """
        Test node launching using an unmanaged image referencing the
        image name as known by the provider.
        """
        configfile = self.setup_config('unmanaged_image_provider_name.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)

        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager._client.create_image(name="provider-named-image")

        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

    def test_paused_gets_declined(self):
        """Test that a paused request, that later gets declined, unpauses."""

        # First config has max-servers set to 2
        configfile = self.setup_config('pause_declined_1.yaml')
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Create a request that uses all capacity (2 servers)
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(len(req.nodes), 2)

        # Now that we have 2 nodes in use, create another request that
        # requests two nodes, which should cause the request to pause.
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('fake-label')
        req2.node_types.append('fake-label')
        self.zk.storeNodeRequest(req2)
        req2 = self.waitForNodeRequest(req2, (zk.PENDING,))

        # Second config decreases max-servers to 1
        self.replace_config(configfile, 'pause_declined_2.yaml')

        # Because the second request asked for 2 nodes, but that now exceeds
        # max-servers, req2 should get declined now, and transition to FAILED
        req2 = self.waitForNodeRequest(req2, (zk.FAILED,))
        self.assertNotEqual(req2.declined_by, [])

    def test_node_auto_floating_ip(self):
        """Test that auto-floating-ip option works fine."""
        configfile = self.setup_config('node_auto_floating_ip.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider1', 'fake-image')
        self.waitForImage('fake-provider2', 'fake-image')
        self.waitForImage('fake-provider3', 'fake-image')
        label1_nodes = self.waitForNodes('fake-label1')
        label2_nodes = self.waitForNodes('fake-label2')
        label3_nodes = self.waitForNodes('fake-label3')

        self.assertEqual(1, len(label1_nodes))
        self.assertEqual(1, len(label2_nodes))
        self.assertEqual(1, len(label3_nodes))

        # auto-floating-ip: False
        self.assertEqual('fake-provider1', label1_nodes[0].provider)
        self.assertEqual('', label1_nodes[0].public_ipv4)
        self.assertEqual('', label1_nodes[0].public_ipv6)
        self.assertEqual('fake', label1_nodes[0].interface_ip)

        # auto-floating-ip: True
        self.assertEqual('fake-provider2', label2_nodes[0].provider)
        self.assertEqual('fake', label2_nodes[0].public_ipv4)
        self.assertEqual('', label2_nodes[0].public_ipv6)
        self.assertEqual('fake', label2_nodes[0].interface_ip)

        # auto-floating-ip: default value
        self.assertEqual('fake-provider3', label3_nodes[0].provider)
        self.assertEqual('fake', label3_nodes[0].public_ipv4)
        self.assertEqual('', label3_nodes[0].public_ipv6)
        self.assertEqual('fake', label3_nodes[0].interface_ip)

    def test_secure_file(self):
        """Test using secure.conf file"""
        configfile = self.setup_config('secure_file_config.yaml')
        securefile = self.setup_secure('secure_file_secure.yaml')
        pool = self.useNodepool(
            configfile,
            secure_conf=securefile,
            watermark_sleep=1)
        self.useBuilder(configfile, securefile=securefile)
        pool.start()
        self.wait_for_config(pool)

        zk_servers = pool.config.zookeeper_servers
        self.assertEqual(1, len(zk_servers))
        key = list(zk_servers.keys())[0]
        self.assertEqual(self.zookeeper_host, zk_servers[key].host)
        self.assertEqual(self.zookeeper_port, zk_servers[key].port)
        self.assertEqual(self.zookeeper_chroot, zk_servers[key].chroot)

        image = self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual(image.username, 'zuul')
        nodes = self.waitForNodes('fake-label')

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertEqual(nodes[0].username, 'zuul')
        self.assertNotEqual(nodes[0].host_keys, [])

    def test_provider_removal(self):
        """Test that removing a provider stops the worker thread"""
        configfile = self.setup_config('launcher_two_provider.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=.5)
        pool.start()
        self.waitForNodes('fake-label')
        self.assertEqual(2, len(pool._pool_threads))

        self.replace_config(configfile, 'launcher_two_provider_remove.yaml')
        # wait longer than our watermark_sleep time for the config to change
        time.sleep(1)
        self.assertEqual(1, len(pool._pool_threads))

    def test_failed_provider(self):
        """Test that broken provider doesn't fail node requests."""
        configfile = self.setup_config('launcher_two_provider_max_1.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=.5)
        pool.start()
        self.wait_for_config(pool)

        # Steady state at images available.
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForImage('fake-provider2', 'fake-image')
        # We have now reached steady state and can manipulate the system to
        # test failing cloud behavior.

        # Make two requests so that the next requests are paused.
        # Note we use different provider specific labels here to avoid
        # a race where a single provider fulfills both of these initial
        # requests.

        # fake-provider
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label2')
        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req, zk.FULFILLED)

        # fake-provider2
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label3')
        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req, zk.FULFILLED)

        nodes = map(pool.zk.getNode, pool.zk.getNodes())
        provider1_first = None
        provider2_first = None
        for node in nodes:
            if node.provider == 'fake-provider2':
                provider2_first = node
            elif node.provider == 'fake-provider':
                provider1_first = node

        # Mark the nodes as being used so they won't be deleted at pause.
        # Locking them is enough.
        self.zk.lockNode(provider1_first, blocking=False)
        self.zk.lockNode(provider2_first, blocking=False)

        # Next two requests will go pending one for each provider.
        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.node_types.append('fake-label')
        self.zk.storeNodeRequest(req1)
        req1 = self.waitForNodeRequest(req1, zk.PENDING)

        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('fake-label')
        self.zk.storeNodeRequest(req2)
        req2 = self.waitForNodeRequest(req2, zk.PENDING)

        # Delete node attached to provider2 this will cause provider2 to
        # fulfill the request it had pending.
        provider2_first.state = zk.DELETING
        self.zk.storeNode(provider2_first)
        self.zk.unlockNode(provider2_first)
        self.waitForNodeDeletion(provider2_first)

        while True:
            # Wait for provider2 node to be created. Also find the request
            # that was not fulfilled. This is the request that fake-provider
            # is pending on.
            req = self.zk.getNodeRequest(req1.id)
            if req.state == zk.FULFILLED:
                final_req = req2
                break
            req = self.zk.getNodeRequest(req2.id)
            if req.state == zk.FULFILLED:
                final_req = req1
                break

        provider2_second = None
        nodes = map(pool.zk.getNode, pool.zk.getNodes())
        for node in nodes:
            if (node and node.provider == 'fake-provider2' and
                    node.state == zk.READY):
                provider2_second = node
                break

        # Now delete the new node we had provider2 build. At this point,
        # the only provider with any requests is fake-provider.
        provider2_second.state = zk.DELETING
        self.zk.storeNode(provider2_second)

        # Set provider1 run_handler to throw exception to simulate a
        # broken cloud. Note the pool worker instantiates request handlers on
        # demand which is why we have a somewhat convoluted monkey patch here.
        # We must patch deep enough in the request handler that
        # despite being paused fake-provider will still trip over this code.
        pool_worker = pool.getPoolWorkers('fake-provider')[0]
        request_handler = pool_worker.request_handlers[0]

        def raise_KeyError(node):
            raise KeyError('fake-provider')

        request_handler.launch_manager.launch = raise_KeyError

        # Delete instance in fake-provider. This should cause provider2
        # to service the request that was held pending by fake-provider.
        provider1_first.state = zk.DELETING
        self.zk.storeNode(provider1_first)
        self.zk.unlockNode(provider1_first)

        # Request is fulfilled by provider 2
        req = self.waitForNodeRequest(final_req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(1, len(req.declined_by))
        self.assertIn('fake-provider-main', req.declined_by[0])

    def test_disabled_provider(self):
        '''
        A request should fail even with a provider that is disabled by
        setting max-servers to 0. Because we look to see that all providers
        decline a request by comparing the declined_by request attribute to
        the list of registered launchers, this means that each must attempt
        to handle it at least once, and thus decline it.
        '''
        configfile = self.setup_config('disabled_provider.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FAILED)

    def test_provider_wont_wedge(self):
        '''
        A provider should not wedge itself when it is at (1) maximum capacity
        (# registered nodes == max-servers), (2) all of its current nodes are
        not being used, and (3) a request comes in with a label that it does
        not yet have available. Normally, situation (3) combined with (1)
        would cause the provider to pause until capacity becomes available,
        but because of (2), it never will and we would wedge the provider.
        '''
        configfile = self.setup_config('wedge_test.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Wait for fake-label1 min-ready request to be fulfilled, which will
        # put us at maximum capacity with max-servers of 1.
        label1_nodes = self.waitForNodes('fake-label1')
        self.assertEqual(1, len(label1_nodes))

        # Now we submit a request for fake-label2, which is not yet available.
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label2')
        self.zk.storeNodeRequest(req)

        # The provider should pause here to handle the fake-label2 request.
        # But because the fake-label1 node is not being used, and will never
        # be freed because we are paused and not handling additional requests,
        # the pool worker thread should recognize that and delete the unused
        # fake-label1 node for us. It can then fulfill the fake-label2 request.
        self.waitForNodeDeletion(label1_nodes[0])
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
