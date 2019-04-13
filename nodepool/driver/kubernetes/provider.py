# Copyright 2018 Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import urllib3
import time

from kubernetes.config import config_exception as kce
from kubernetes import client as k8s_client
from openshift import config

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.driver.kubernetes import handler

urllib3.disable_warnings()


class KubernetesProvider(Provider):
    log = logging.getLogger("nodepool.driver.kubernetes.KubernetesProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ready = False
        try:
            self.k8s_client, self.rbac_client = self._get_client(
                provider.context)
        except kce.ConfigException:
            self.log.exception("Couldn't load client from config")
            self.log.info("Get context list using this command: "
                          "python3 -c \"from openshift import config; "
                          "print('\\n'.join([i['name'] for i in "
                          "config.list_kube_config_contexts()[0]]))\"")
            self.k8s_client = None
            self.rbac_client = None
        self.namespace_names = set()
        for pool in provider.pools.values():
            self.namespace_names.add(pool.name)

    def _get_client(self, context):
        conf = config.new_client_from_config(context=context)
        return (
            k8s_client.CoreV1Api(conf),
            k8s_client.RbacAuthorizationV1beta1Api(conf))

    def start(self, zk_conn):
        self.log.debug("Starting")
        if self.ready or not self.k8s_client or not self.rbac_client:
            return
        self.ready = True

    def stop(self):
        self.log.debug("Stopping")
        self.ready = False

    def listNodes(self):
        servers = []

        class FakeServer:
            def __init__(self, namespace, provider, valid_names):
                self.id = namespace.metadata.name
                self.name = namespace.metadata.name
                self.metadata = {}

                if [True for valid_name in valid_names
                    if namespace.metadata.name.startswith("%s-" % valid_name)]:
                    node_id = namespace.metadata.name.split('-')[-1]
                    try:
                        # Make sure last component of name is an id
                        int(node_id)
                        self.metadata['nodepool_provider_name'] = provider
                        self.metadata['nodepool_node_id'] = node_id
                    except Exception:
                        # Probably not a managed namespace, let's skip metadata
                        pass

            def get(self, name, default=None):
                return getattr(self, name, default)

        if self.ready:
            for namespace in self.k8s_client.list_namespace().items:
                servers.append(FakeServer(
                    namespace, self.provider.name, self.namespace_names))
        return servers

    def labelReady(self, name):
        # Labels are always ready
        return True

    def join(self):
        pass

    def cleanupLeakedResources(self):
        pass

    def cleanupNode(self, server_id):
        if not self.ready:
            return
        self.log.debug("%s: removing namespace" % server_id)
        delete_body = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "propagationPolicy": "Background"
        }
        try:
            self.k8s_client.delete_namespace(server_id, body=delete_body)
            self.log.info("%s: namespace removed" % server_id)
        except Exception:
            # TODO: implement better exception handling
            self.log.exception("Couldn't remove namespace %s" % server_id)

    def waitForNodeCleanup(self, server_id):
        for retry in range(300):
            try:
                self.k8s_client.read_namespace(server_id)
            except Exception:
                break
            time.sleep(1)

    def createNamespace(self, node, pool, restricted_access=False):
        name = node.id
        namespace = "%s-%s" % (pool, name)
        user = "zuul-worker"

        self.log.debug("%s: creating namespace" % namespace)
        # Create the namespace
        ns_body = {
            'apiVersion': 'v1',
            'kind': 'Namespace',
            'metadata': {
                'name': namespace,
                'nodepool_node_id': name
            }
        }
        proj = self.k8s_client.create_namespace(ns_body)
        node.external_id = namespace

        # Create the service account
        sa_body = {
            'apiVersion': 'v1',
            'kind': 'ServiceAccount',
            'metadata': {'name': user}
        }
        self.k8s_client.create_namespaced_service_account(namespace, sa_body)

        # Wait for the token to be created
        for retry in range(30):
            sa = self.k8s_client.read_namespaced_service_account(
                user, namespace)
            ca_crt = None
            token = None
            if sa.secrets:
                for secret_obj in sa.secrets:
                    secret = self.k8s_client.read_namespaced_secret(
                        secret_obj.name, namespace)
                    ca_crt = secret.data.get('ca.crt')
                    token = secret.data.get('token')
                    if token and ca_crt:
                        break
            if token and ca_crt:
                break
            time.sleep(1)
        if not token or not ca_crt:
            raise exceptions.LaunchNodepoolException(
                "%s: couldn't find token for service account %s" %
                (namespace, sa))

        # Create service account role
        all_verbs = ["create", "delete", "get", "list", "patch",
                     "update", "watch"]
        if restricted_access:
            role_name = "zuul-restricted"
            role_body = {
                'kind': 'Role',
                'apiVersion': 'rbac.authorization.k8s.io/v1beta1',
                'metadata': {
                    'name': role_name,
                },
                'rules': [{
                    'apiGroups': [""],
                    'resources': ["pods"],
                    'verbs': ["get", "list"],
                }, {
                    'apiGroups': [""],
                    'resources': ["pods/exec"],
                    'verbs': all_verbs
                }, {
                    'apiGroups': [""],
                    'resources': ["pods/logs"],
                    'verbs': all_verbs
                }]
            }
        else:
            role_name = "zuul"
            role_body = {
                'kind': 'Role',
                'apiVersion': 'rbac.authorization.k8s.io/v1beta1',
                'metadata': {
                    'name': role_name,
                },
                'rules': [{
                    'apiGroups': [""],
                    'resources': ["pods", "pods/exec", "pods/log",
                                  "services", "endpoints", "crontabs", "jobs",
                                  "deployments", "replicasets",
                                  "configmaps", "secrets"],
                    'verbs': all_verbs,
                }]
            }
        self.rbac_client.create_namespaced_role(namespace, role_body)

        # Give service account admin access
        role_binding_body = {
            'apiVersion': 'rbac.authorization.k8s.io/v1beta1',
            'kind': 'RoleBinding',
            'metadata': {'name': 'zuul-role'},
            'roleRef': {
                'apiGroup': 'rbac.authorization.k8s.io',
                'kind': 'Role',
                'name': role_name,
            },
            'subjects': [{
                'kind': 'ServiceAccount',
                'name': user,
                'namespace': namespace,
            }],
            'userNames': ['system:serviceaccount:%s:zuul-worker' % namespace]
        }
        self.rbac_client.create_namespaced_role_binding(
            namespace, role_binding_body)

        resource = {
            'name': proj.metadata.name,
            'namespace': namespace,
            'host': self.k8s_client.api_client.configuration.host,
            'skiptls': not self.k8s_client.api_client.configuration.verify_ssl,
            'token': token,
            'ca_crt': ca_crt,
            'user': user,
        }
        self.log.info("%s: namespace created" % namespace)
        return resource

    def createPod(self, node, pool, label):
        resource = self.createNamespace(node, pool, restricted_access=True)
        namespace = resource['namespace']
        pod_body = {
            'apiVersion': 'v1',
            'kind': 'Pod',
            'metadata': {'name': label.name},
            'spec': {
                'containers': [{
                    'name': label.name,
                    'image': label.image,
                    'imagePullPolicy': label.image_pull,
                    'command': ["/bin/bash", "-c", "--"],
                    'args': ["while true; do sleep 30; done;"],
                    'workingDir': '/tmp'
                }]
            },
            'restartPolicy': 'Never',
        }
        self.k8s_client.create_namespaced_pod(namespace, pod_body)
        for retry in range(300):
            pod = self.k8s_client.read_namespaced_pod(label.name, namespace)
            if pod.status.phase == "Running":
                break
            self.log.debug("%s: pod status is %s", namespace, pod.status.phase)
            time.sleep(1)
        if retry == 299:
            raise exceptions.LaunchNodepoolException(
                "%s: pod failed to initialize (%s)" % (
                    namespace, pod.status.phase))
        resource["pod"] = label.name
        return resource

    def getRequestHandler(self, poolworker, request):
        return handler.KubernetesNodeRequestHandler(poolworker, request)
