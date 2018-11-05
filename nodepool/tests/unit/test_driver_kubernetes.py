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
from nodepool.driver.kubernetes import provider


class FakeCoreClient(object):
    def __init__(self):
        self.namespaces = []

        class FakeApi:
            class configuration:
                host = "http://localhost:8080"
                verify_ssl = False
        self.api_client = FakeApi()

    def list_namespace(self):
        class FakeNamespaces:
            items = self.namespaces
        return FakeNamespaces

    def create_namespace(self, ns_body):
        class FakeNamespace:
            class metadata:
                name = ns_body['metadata']['name']
        self.namespaces.append(FakeNamespace)
        return FakeNamespace

    def delete_namespace(self, name, delete_body):
        to_delete = None
        for namespace in self.namespaces:
            if namespace.metadata.name == name:
                to_delete = namespace
                break
        if not to_delete:
            raise RuntimeError("Unknown namespace %s" % name)
        self.namespaces.remove(to_delete)

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
            data = {'ca.crt': 'fake-ca', 'token': 'fake-token'}
        return FakeSecret

    def create_namespaced_pod(self, ns, pod_body):
        return

    def read_namespaced_pod(self, name, ns):
        class FakePod:
            class status:
                phase = "Running"
        return FakePod


class FakeRbacClient(object):
    def create_namespaced_role(self, ns, role_body):
        return

    def create_namespaced_role_binding(self, ns, role_binding_body):
        return


class TestDriverKubernetes(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverKubernetes")

    def setUp(self):
        super().setUp()
        self.fake_k8s_client = FakeCoreClient()
        self.fake_rbac_client = FakeRbacClient()

        def fake_get_client(*args):
            return self.fake_k8s_client, self.fake_rbac_client

        self.useFixture(fixtures.MockPatchObject(
            provider.KubernetesProvider, '_get_client',
            fake_get_client
        ))

    def test_kubernetes_machine(self):
        configfile = self.setup_config('kubernetes.yaml')
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

    def test_kubernetes_native(self):
        configfile = self.setup_config('kubernetes.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('kubernetes-namespace')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'namespace')
        self.assertEqual(node.connection_port.get('token'), 'fake-token')

        node.state = zk.DELETING
        self.zk.storeNode(node)

        self.waitForNodeDeletion(node)
