#!/usr/bin/env python

# Copyright (C) 2011-2013 OpenStack Foundation
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
#
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

import myjenkins
import fakeprovider
from task_manager import Task, TaskManager


class CreateNodeTask(Task):
    def main(self, jenkins):
        if 'credentials_id' in self.args:
            launcher_params = {'port': 22,
                               'credentialsId': self.args['credentials_id'],
                               'host': self.args['host']}
        else:
            launcher_params = {'port': 22,
                               'username': self.args['username'],
                               'privatekey': self.args['private_key'],
                               'host': self.args['host']}
        try:
            jenkins.create_node(
                name=self.args['name'],
                numExecutors=self.args['executors'],
                nodeDescription=self.args['description'],
                remoteFS=self.args['root'],
                labels=self.args['labels'],
                exclusive=True,
                launcher='hudson.plugins.sshslaves.SSHLauncher',
                launcher_params=launcher_params)
        except myjenkins.JenkinsException as e:
            if 'already exists' in str(e):
                pass
            else:
                raise


class NodeExistsTask(Task):
    def main(self, jenkins):
        return jenkins.node_exists(self.args['name'])


class DeleteNodeTask(Task):
    def main(self, jenkins):
        return jenkins.delete_node(self.args['name'])


class JenkinsManager(TaskManager):
    log = logging.getLogger("nodepool.JenkinsManager")

    def __init__(self, target):
        super(JenkinsManager, self).__init__(None, target.name, target.rate)
        self.target = target
        self._client = self._getClient()

    def _getClient(self):
        if self.target.jenkins_apikey == 'fake':
            return fakeprovider.FakeJenkins()
        return myjenkins.Jenkins(self.target.jenkins_url,
                                 self.target.jenkins_user,
                                 self.target.jenkins_apikey)

    def createNode(self, name, host, description, labels, executors, root,
                   credentials_id=None, username=None, private_key=None):
        args = dict(name=name, host=host, description=description,
                    labels=labels, executors=executors, root=root)
        if credentials_id:
            args['credentials_id'] = credentials_id
        else:
            args['username'] = username
            args['private_key'] = private_key
        return self.submitTask(CreateNodeTask(**args))

    def nodeExists(self, name):
        return self.submitTask(NodeExistsTask(name=name))

    def deleteNode(self, name):
        return self.submitTask(DeleteNodeTask(name=name))
