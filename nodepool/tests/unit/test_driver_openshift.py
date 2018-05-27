# Copyright (C) 2018 Red Hat
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

import fixtures
import logging

from nodepool import tests
from nodepool import zk
from nodepool.driver.openshift import provider


class FakeOpenshiftClient(object):
    def __init__(self):
        self.projects = []

        class FakeApi:
            class configuration:
                host = "http://localhost:8080"
                verify_ssl = False
        self.api_client = FakeApi()

    def list_project(self):
        class FakeProjects:
            items = self.projects
        return FakeProjects

    def create_project_request(self, proj_body):
        class FakeProject:
            class metadata:
                name = proj_body['metadata']['name']
        self.projects.append(FakeProject)
        return FakeProject

    def delete_project(self, name):
        to_delete = None
        for project in self.projects:
            if project.metadata.name == name:
                to_delete = project
                break
        if not to_delete:
            raise RuntimeError("Unknown project %s" % name)
        self.projects.remove(to_delete)

    def create_namespaced_role_binding(self, ns, role_binding_body):
        return


class FakeCoreClient(object):
    def create_namespaced_service_account(self, ns, sa_body):
        return

    def read_namespaced_service_account(self, user, ns):
        class FakeSA:
            class secret:
                name = "fake"
        FakeSA.secrets = [FakeSA.secret]
        return FakeSA

    def read_namespaced_secret(self, name, ns):
        class FakeSecret:
            class metadata:
                annotations = {'openshift.io/token-secret.value': 'fake-token'}
        return FakeSecret

    def create_namespaced_pod(self, ns, pod_body):
        return

    def read_namespaced_pod(self, name, ns):
        class FakePod:
            class status:
                phase = "Running"
        return FakePod


class TestDriverOpenshift(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverOpenshift")

    def setUp(self):
        super().setUp()
        self.fake_os_client = FakeOpenshiftClient()
        self.fake_k8s_client = FakeCoreClient()

        def fake_get_client(*args):
            return self.fake_os_client, self.fake_k8s_client

        self.useFixture(fixtures.MockPatchObject(
            provider.OpenshiftProvider, '_get_client',
            fake_get_client
        ))

    def test_openshift_machine(self):
        configfile = self.setup_config('openshift.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('pod-fedora')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'kubectl')
        self.assertEqual(node.connection_port.get('token'), 'fake-token')

        node.state = zk.DELETING
        self.zk.storeNode(node)

        self.waitForNodeDeletion(node)

    def test_openshift_native(self):
        configfile = self.setup_config('openshift.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('openshift-project')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'project')
        self.assertEqual(node.connection_port.get('token'), 'fake-token')

        node.state = zk.DELETING
        self.zk.storeNode(node)

        self.waitForNodeDeletion(node)
