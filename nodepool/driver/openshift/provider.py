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
from openshift import client as os_client
from openshift import config

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.driver.openshift import handler

urllib3.disable_warnings()


class OpenshiftProvider(Provider):
    log = logging.getLogger("nodepool.driver.openshift.OpenshiftProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ready = False
        try:
            self.os_client, self.k8s_client = self._get_client(
                provider.context)
        except kce.ConfigException:
            self.log.exception(
                "Couldn't load context %s from config", provider.context)
            self.os_client = None
            self.k8s_client = None
        self.project_names = set()
        for pool in provider.pools.values():
            self.project_names.add(pool.name)

    def _get_client(self, context):
        conf = config.new_client_from_config(context=context)
        return (
            os_client.OapiApi(conf),
            k8s_client.CoreV1Api(conf))

    def start(self, zk_conn):
        self.log.debug("Starting")
        if self.ready or not self.os_client or not self.k8s_client:
            return
        self.ready = True

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        class FakeServer:
            def __init__(self, project, provider, valid_names):
                self.id = project.metadata.name
                self.name = project.metadata.name
                self.metadata = {}

                if [True for valid_name in valid_names
                    if project.metadata.name.startswith("%s-" % valid_name)]:
                    node_id = project.metadata.name.split('-')[-1]
                    try:
                        # Make sure last component of name is an id
                        int(node_id)
                        self.metadata['nodepool_provider_name'] = provider
                        self.metadata['nodepool_node_id'] = node_id
                    except Exception:
                        # Probably not a managed project, let's skip metadata
                        pass

            def get(self, name, default=None):
                return getattr(self, name, default)

        if self.ready:
            for project in self.os_client.list_project().items:
                servers.append(FakeServer(
                    project, self.provider.name, self.project_names))
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
        self.log.debug("%s: removing project" % server_id)
        try:
            self.os_client.delete_project(server_id)
            self.log.info("%s: project removed" % server_id)
        except Exception:
            # TODO: implement better exception handling
            self.log.exception("Couldn't remove project %s" % server_id)

    def waitForNodeCleanup(self, server_id):
        for retry in range(300):
            try:
                self.os_client.read_project(server_id)
            except Exception:
                break
            time.sleep(1)

    def createProject(self, project):
        self.log.debug("%s: creating project" % project)
        # Create the project
        proj_body = {
            'apiVersion': 'v1',
            'kind': 'ProjectRequest',
            'metadata': {
                'name': project,
            }
        }
        self.os_client.create_project_request(proj_body)
        return project

    def prepareProject(self, project):
        user = "zuul-worker"

        # Create the service account
        sa_body = {
            'apiVersion': 'v1',
            'kind': 'ServiceAccount',
            'metadata': {'name': user}
        }
        self.k8s_client.create_namespaced_service_account(project, sa_body)

        # Wait for the token to be created
        for retry in range(30):
            sa = self.k8s_client.read_namespaced_service_account(
                user, project)
            token = None
            if sa.secrets:
                for secret_obj in sa.secrets:
                    secret = self.k8s_client.read_namespaced_secret(
                        secret_obj.name, project)
                    token = secret.metadata.annotations.get(
                        'openshift.io/token-secret.value')
                    if token:
                        break
            if token:
                break
            time.sleep(1)
        if not token:
            raise exceptions.LaunchNodepoolException(
                "%s: couldn't find token for service account %s" %
                (project, sa))

        # Give service account admin access
        role_body = {
            'apiVersion': 'v1',
            'kind': 'RoleBinding',
            'metadata': {'name': 'admin-0'},
            'roleRef': {'name': 'admin'},
            'subjects': [{
                'kind': 'ServiceAccount',
                'name': user,
                'namespace': project,
            }],
            'userNames': ['system:serviceaccount:%s:zuul-worker' % project]
        }
        try:
            self.os_client.create_namespaced_role_binding(project, role_body)
        except ValueError:
            # https://github.com/ansible/ansible/issues/36939
            pass

        resource = {
            'namespace': project,
            'host': self.os_client.api_client.configuration.host,
            'skiptls': not self.os_client.api_client.configuration.verify_ssl,
            'token': token,
            'user': user,
        }
        self.log.info("%s: project created" % project)
        return resource

    def createPod(self, project, label):
        spec_body = {
            'name': label.name,
            'image': label.image,
            'imagePullPolicy': label.image_pull,
            'command': ["/bin/bash", "-c", "--"],
            'args': ["while true; do sleep 30; done;"],
            'workingDir': '/tmp',
        }
        if label.cpu or label.memory:
            spec_body['resources'] = {}
            for rtype in ('requests', 'limits'):
                rbody = {}
                if label.cpu:
                    rbody['cpu'] = int(label.cpu)
                if label.memory:
                    rbody['memory'] = '%dMi' % int(label.memory)
                spec_body['resources'][rtype] = rbody
        pod_body = {
            'apiVersion': 'v1',
            'kind': 'Pod',
            'metadata': {'name': label.name},
            'spec': {
                'containers': [spec_body],
            },
            'restartPolicy': 'Never',
        }
        self.k8s_client.create_namespaced_pod(project, pod_body)
        for retry in range(300):
            pod = self.k8s_client.read_namespaced_pod(label.name, project)
            if pod.status.phase == "Running":
                break
            self.log.debug("%s: pod status is %s", project, pod.status.phase)
            time.sleep(1)
        if retry == 299:
            raise exceptions.LaunchNodepoolException(
                "%s: pod failed to initialize (%s)" % (
                    project, pod.status.phase))

    def getRequestHandler(self, poolworker, request):
        return handler.OpenshiftNodeRequestHandler(poolworker, request)
